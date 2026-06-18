import os
import tempfile
import pytest


@pytest.fixture
def python_file():
    code = '''
import os
from datetime import datetime

def helper():
    return "helper"

class UserService:
    def login(self, username: str, password: str) -> bool:
        result = self._validate(username, password)
        return result

    def _validate(self, username: str, password: str) -> bool:
        hashed = hash(password)
        return helper() is not None

def main():
    svc = UserService()
    return svc.login("admin", "secret")
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    yield path
    os.unlink(path)


class TestASTParserPython:
    @pytest.mark.asyncio
    async def test_parse_file_extracts_functions(self, python_file):
        from spma.ingestion.code.ast_parser import ASTParser
        parser = ASTParser()
        result = await parser.parse_file(python_file)
        func_names = [f["name"] for f in result.get("functions", [])]
        assert "helper" in func_names
        assert "login" in func_names
        assert "_validate" in func_names
        assert "main" in func_names

    @pytest.mark.asyncio
    async def test_parse_file_extracts_classes(self, python_file):
        from spma.ingestion.code.ast_parser import ASTParser
        parser = ASTParser()
        result = await parser.parse_file(python_file)
        class_names = [c["name"] for c in result.get("classes", [])]
        assert "UserService" in class_names

    @pytest.mark.asyncio
    async def test_parse_file_extracts_calls(self, python_file):
        from spma.ingestion.code.ast_parser import ASTParser
        parser = ASTParser()
        result = await parser.parse_file(python_file)
        calls = result.get("calls", [])
        assert len(calls) > 0

    @pytest.mark.asyncio
    async def test_parse_file_extracts_imports(self, python_file):
        from spma.ingestion.code.ast_parser import ASTParser
        parser = ASTParser()
        result = await parser.parse_file(python_file)
        imports = result.get("imports", [])
        assert len(imports) >= 2

    @pytest.mark.asyncio
    async def test_unsupported_extension_returns_empty(self):
        from spma.ingestion.code.ast_parser import ASTParser
        parser = ASTParser()
        result = await parser.parse_file("/path/to/file.unknown")
        assert result["functions"] == []
        assert result["calls"] == []
