"""Une los datos de vocabulario con la plantilla y genera index.html (página publicable)."""
from pathlib import Path
root = Path(__file__).parent
words = "\n".join((root / "src" / f"words-{i}.js").read_text(encoding="utf-8") for i in range(1, 5))
html = (root / "src" / "app.html").read_text(encoding="utf-8").replace("/*@@WORDS@@*/", words)
(root / "index.html").write_text(html, encoding="utf-8")
print(f"index.html: {len(html)/1024:.0f} KB")
