"""Read-only, authorized-source Yosys import. No shell or flattened hierarchy."""
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from .schemas import QueryResult, failure


class YosysRunner:
    def __init__(self, authorized_roots, executable='yosys', timeout=30):
        self.roots = tuple(Path(p).resolve() for p in authorized_roots)
        found = shutil.which(executable)
        self.executable = str(Path(found).resolve()) if found else None
        self.timeout = timeout

    def _source(self, value):
        path = Path(value).resolve(strict=True)
        if not any(path.is_relative_to(root) for root in self.roots):
            raise PermissionError('Source is outside authorized roots')
        if not path.is_file():
            raise ValueError('Source is not a regular file')
        return path

    def load_netlist(self, path, liberty_paths, top=None):
        if not self.executable:
            return failure('yosys_unavailable', 'Yosys executable was not found', unsupported=True)
        if top is not None and (not isinstance(top, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', top)):
            return failure('unsupported_top_name', 'Use a simple Verilog top identifier', unsupported=True)
        try:
            sources = [self._source(p) for p in [path, *liberty_paths]]
            contents = [p.read_bytes() for p in sources]
            # Registered standalone inputs only; never allow preprocessor file access.
            if re.search(rb'`\s*(?:include|readmem)\b|\$readmem', contents[0]):
                return failure('external_dependency', 'External file reads are unsupported', unsupported=True)
            with tempfile.TemporaryDirectory(prefix='scan-verifier-') as work:
                directory = Path(work)
                for i, content in enumerate(contents):
                    (directory / f'source_{i}').write_bytes(content)
                script = '\n'.join([f'read_liberty -lib source_{i}' for i in range(1, len(sources))] + [
                    'read_verilog source_0',
                    f'hierarchy -check -top {top}' if top else 'hierarchy -check -auto-top',
                    'write_json design.json'])
                (directory / 'read.ys').write_text(script)
                process = subprocess.run([self.executable, '-Q', '-T', '-s', str(directory / 'read.ys')],
                    cwd=directory, capture_output=True, text=True, timeout=self.timeout, shell=False)
                log = process.stdout + process.stderr
                if process.returncode:
                    code = 'missing_top' if top is not None and re.search(r'ERROR: Module .*not found', log) is not None else 'yosys_parse_error'
                    return QueryResult('error', errors=[{'code': code, 'message': log}])
                document = json.loads((directory / 'design.json').read_text())
                if top is None:
                    candidates = [name for name, module in document['modules'].items()
                                  if str(module.get('attributes', {}).get('top', '0')).endswith('1')]
                    if len(candidates) != 1:
                        return failure('ambiguous_top', 'Cannot identify one top module')
                    top = candidates[0]
                identity = {'top': top, 'sources': [
                    {'source': str(p), 'sha256': sha256(content).hexdigest()}
                    for p, content in zip(sources, contents)]}
                return QueryResult('ok', {'document': document, 'identity': identity, 'log': log},
                                   evidence=identity['sources'])
        except PermissionError as exc:
            return failure('unauthorized_path', exc)
        except subprocess.TimeoutExpired:
            return failure('yosys_timeout', 'Yosys import exceeded its timeout')
        except (OSError, ValueError, KeyError) as exc:
            return failure('invalid_input', exc)
