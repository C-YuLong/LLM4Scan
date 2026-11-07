"""Build the official ZIP from production files and the team's DashScope key.

The archive contains a credential: keep it local/private, never commit or share it.
No Codex credentials, official datasets, benchmarks or development results are read.
"""
import argparse
import os
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='New ZIP under dev/results (contains the team API key)')
    args = parser.parse_args()
    parser.error('Credential packaging is disabled by user instruction. No machine API key will be read or copied into a ZIP.')
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / 'dev/results') or output.suffix != '.zip':
        parser.error('Keep the credential-bearing ZIP under git-ignored dev/results')
    key = os.environ['DASHSCOPE_API_KEY']
    if not key.strip() or '\n' in key or '\r' in key:
        parser.error('DASHSCOPE_API_KEY must be a nonempty single-line value')
    files = [root / 'Dockerfile', root / '.dockerignore']
    files += sorted(path for path in (root / 'submission').iterdir()
                    if path.is_file() and (path.suffix in {'.py', '.md'} or path.name == 'agent_system'))
    files += sorted((root / 'knowledge').rglob('*.md'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr('.env', 'LLM_API_KEY=' + key + '\n')
    print(f'Created private submission ZIP: {output}')
    print(f'{len(files) + 1} entries; {output.stat().st_size} bytes; .env contains only LLM_API_KEY')


if __name__ == '__main__':
    main()
