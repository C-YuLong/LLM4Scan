"""Small, inspectable keyword retrieval over the project-authored knowledge."""
import argparse
import json
from pathlib import Path
import re


def retrieve(query, limit=3):
    folder = Path(__file__).resolve().parents[1] / 'knowledge/scan-insertion/references'
    ranked, rules = [], []
    lowered = query.casefold()
    commands = set(re.findall(r'\b(?:set|add|rpt|dump|examine|insert)_[a-z_]+\b', lowered))
    drc_rules = set(re.findall(r'\bdftr(?:-[a-z]+\d+|\d+)\b', lowered))
    for path in sorted(folder.glob('*.md')):
        content = path.read_text(encoding='utf-8')
        item = {'source': path.relative_to(folder.parent.parent).as_posix(), 'content': content}
        if path.name == 'rules.md':
            rules.append(item)
            continue
        tags = next(line.split('：', 1)[1] for line in content.splitlines() if line.startswith('检索词：')).split('|')
        matched_tags = [tag.casefold() for tag in tags if tag.casefold() in lowered]
        score = len(matched_tags)
        if score:
            # These files grew into long experiment histories. Retrieve whole
            # relevant entries (with their confidence and scope), not every
            # unrelated example in a matching topic file. Rules stay complete.
            sections = list(re.finditer(r'(?ms)^## .+?(?=^## |\Z)', content))
            scored = []
            for index, section in enumerate(sections):
                body = section.group().casefold()
                section_score = (sum(tag in body for tag in matched_tags) +
                                 3 * sum(command in body for command in commands) +
                                 4 * sum(re.search(r'\b' + re.escape(rule) + r'\b', body) is not None for rule in drc_rules))
                if section_score:
                    scored.append((section_score, index))
            selected = sorted(index for _, index in sorted(scored, key=lambda row: (-row[0], row[1]))[:2])
            if selected:
                item['content'] = content[:sections[0].start()] + '\n'.join(sections[index].group() for index in selected)
                ranked.append((score, path.name, item))
    return rules + [item for _, _, item in sorted(ranked, key=lambda row: (-row[0], row[1]))[:limit]]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('query')
    args = parser.parse_args()
    print(json.dumps(retrieve(args.query), ensure_ascii=False, indent=2))
