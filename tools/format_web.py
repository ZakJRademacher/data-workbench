"""Optional maintenance helper; pip install jsbeautifier cssbeautifier."""
from pathlib import Path
import cssbeautifier
import jsbeautifier

root = Path(__file__).resolve().parents[1] / 'web'
for filename, formatter in [('app.js',jsbeautifier),('style.css',cssbeautifier)]:
    path=root/filename
    options=formatter.default_options()
    options.indent_size=2
    path.write_text(formatter.beautify(path.read_text(encoding='utf-8'), options)+'\n',encoding='utf-8')
print('Formatted JavaScript and CSS.')
