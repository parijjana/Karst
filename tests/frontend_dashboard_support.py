from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


INDEX_PATH = Path(__file__).parents[1] / "src" / "index.html"


class DashboardHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[tuple[str, str, str | None]] = []
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self.meta: list[dict[str, str | None]] = []
        self._script_attributes: dict[str, str | None] | None = None
        self._script_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        self.attributes.extend((tag, name, value) for name, value in attrs)
        if tag == "script":
            self._script_attributes = attributes
            self._script_parts = []
        elif tag == "meta":
            self.meta.append(attributes)

    def handle_data(self, data: str) -> None:
        if self._script_attributes is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_attributes is not None:
            self.scripts.append(
                (self._script_attributes, "".join(self._script_parts))
            )
            self._script_attributes = None
            self._script_parts = []


@pytest.fixture(scope="module")
def dashboard() -> tuple[str, DashboardHTMLParser]:
    source = INDEX_PATH.read_text(encoding="utf-8")
    parser = DashboardHTMLParser()
    parser.feed(source)
    return source, parser


def run_logout_harness(
    dashboard: tuple[str, DashboardHTMLParser], scenario: str
) -> dict[str, object]:
    _source, parser = dashboard
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for logout behavior checking")
    dashboard_script = "\n".join(source for _attributes, source in parser.scripts)
    prelude = """
        const elements = new Map();
        function fakeElement() {
            return {
                hidden: false,
                disabled: false,
                textContent: '',
                value: '',
                className: '',
                style: {},
                dataset: {},
                classList: { add() {}, remove() {}, toggle() {} },
                addEventListener() {},
                appendChild() {},
                replaceChildren() {},
                setAttribute() {}
            };
        }
        globalThis.document = {
            body: fakeElement(),
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, fakeElement());
                return elements.get(id);
            },
            querySelectorAll() { return []; },
            addEventListener() {},
            createElement() { return fakeElement(); },
            createDocumentFragment() { return fakeElement(); }
        };
        globalThis.window = {
            setTimeout,
            clearTimeout,
            innerWidth: 1200
        };
    """
    result = subprocess.run(
        [node, "-"],
        input=f"{prelude}\n{dashboard_script}\n{scenario}",
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])
