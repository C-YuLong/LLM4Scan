"""Bounded subprocess source parsing and lossless, chunk-addressable views."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

SOURCE_TOOLS = {'read_tcl_commands', 'read_verilog_blocks'}
GUIDE = '''Source reading guide:
Use read_tcl_commands(artifact="candidate.dofile",command="set_scan_cell_mapping",cursor=0) for exact commands, full arguments and enclosing contexts; null command lists all commands. Variables/substitutions remain unevaluated. Comments and literal strings are not commands.
Use read_verilog_blocks(artifact=<registered stage .v>,kind="instance",module="openE902",name="x_cr_clkrst_top",top=null,instance_path=null,cursor=0) for a complete local instance declaration; kind="module",module=null,name=null lists modules. Null name browses blocks; select names before paging large files. Local names are not elaborated hierarchy paths: use query_design for hierarchy, query_cell_connections for local pin populations, and trace_connection for a concrete pin to a top input. Prefer read_verilog_blocks with top+instance_path and module/name=null for an actual instance declaration or referenced definition.
Copy next_read.tool and next_read.arguments exactly. continuation=current_block means this declaration is incomplete; more_blocks means another block remains. Only continue when omitted content matters. Positions are 1-based, end exclusive; text chunks concatenate exactly to the original block.
Choose before/after files using stage_artifacts and compare_design to verify actual changes. Read logs for execution; query_report log_events/log_members for complete replacement populations. Source FACT is not program proof. Choose evidence by what the original requirement asks to establish. Script commands can establish configuration; they do not by themselves show execution or resulting connectivity. Explain why the cited observations support the requirement; no file category is universally required. Parser errors/partial results cannot prove absence.
'''

def parse_source(language, text, deadline):
    if language == 'verilog' and re.search(r'`\s*(?:include|define|ifdef|ifndef|elsif|else|endif|undef)\b|\$readmem', text):
        return dict(complete=False, blocks=[], diagnostics=['External dependencies and preprocessing directives are unsupported; use read_lines.'])
    remaining=min(15.0, deadline-time.monotonic())
    if remaining <= 0:
        return dict(complete=False,blocks=[],diagnostics=['Source parsing deadline exhausted; use read_lines.'])
    try:
        process=subprocess.run([sys.executable,str(Path(__file__).resolve()),language],input=text,
                               text=True,capture_output=True,timeout=remaining,check=False)
        if process.returncode:
            return dict(complete=False,blocks=[],diagnostics=['Source parser unavailable or failed; install verifier source-reader requirements, or use read_lines/search_text.'])
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return dict(complete=False,blocks=[],diagnostics=['Source parser timed out and was terminated; use read_lines/search_text.'])
    except (OSError,ValueError):
        return dict(complete=False,blocks=[],diagnostics=['Source parser execution failed; use read_lines/search_text.'])

def source_view(value, cursor, limit):
    blocks=value['blocks']; parameters=value['parameters']; tool=value['operation']
    # cursor is a deterministic chunk index, not a source line or a byte offset.
    chunks=[]
    for block in blocks:
        text=block['text']; width=value.get('source_chunk_chars',max(1,min(1200,limit//12)))
        for offset in range(0,max(1,len(text)),width):
            chunks.append((block,offset,text[offset:offset+width]))
    if cursor < 0 or cursor > len(chunks):raise ValueError('cursor outside source block pages')
    base={k:v for k,v in value.items() if k not in {'blocks','rows','next_read','partial','omitted','source_chunk_chars'}}
    base.update(total_blocks=len(blocks),total_chunks=len(chunks),cursor=cursor)
    if cursor==len(chunks):return dict(base,rows=[],next_read=None,next_cursor=None,omitted=False,partial=False,display_complete=True)
    rows=[]
    def result():
        next_index=cursor+len(rows)
        next_cursor=next_index if next_index<len(chunks) else None
        last=rows[-1]
        continuation=('current_block' if last['char_end']<last['block_chars'] else 'more_blocks') if next_cursor is not None else None
        return dict(base,rows=list(rows),next_cursor=next_cursor,
                    partial=any(not r['block_complete'] for r in rows),omitted=next_cursor is not None or cursor>0,
                    display_complete=cursor==0 and next_cursor is None,
                    continuation=continuation,next_read=None if next_cursor is None else dict(tool=tool,arguments={**parameters,'cursor':next_cursor}))
    for block,offset,text in chunks[cursor:]:
        row={k:v for k,v in block.items() if k!='text'}
        row.update(text=text,char_start=offset,char_end=offset+len(text),block_chars=len(block['text']),
                   block_complete=offset==0 and offset+len(text)==len(block['text']))
        rows.append(row)
        answer=result()
        if len(json.dumps(answer,ensure_ascii=False))>limit:
            rows.pop()
            if not rows:raise ValueError('Source block metadata exceeds result budget; increase max_tool_result_chars or narrow query')
            return result()
    return result()

def worker(language,text):
    lines=text.splitlines(keepends=True); offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    def position(line,col):return offsets[line-1]+col-1
    blocks=[]
    def add(kind,name,start,end,**extra):
        a=position(*start);b=position(*end)
        block=dict(block_id=f'{kind}:{a}:{b}:{name}',kind=kind,name=name,start=list(start),end=list(end),text=text[a:b],**extra)
        blocks.append(block);return block
    try:
        if language=='tcl':
            from tclint.parser import Parser
            root=Parser().parse(text)
            def walk(n,parents):
                context=parents
                if type(n).__name__=='Command':
                    name=n.routine.value if type(n.routine).__name__ in {'BareWord','QuotedWord','BracedWord'} else None
                    args=[dict(kind=type(a).__name__,start=[a.line,a.col],end=list(a.end_pos)) for a in n.args]
                    block=add('command',name,(n.line,n.col),n.end_pos,arguments=args,contexts=parents)
                    context=parents+[dict(block_id=block['block_id'],name=name,start=block['start'],end=block['end'])]
                for child in n.children:walk(child,context)
            walk(root,[]);diagnostics=[]
        else:
            import pyslang
            tree=pyslang.syntax.SyntaxTree.fromText(text); sm=tree.sourceManager
            def loc(p):
                line=sm.getLineNumber(p); byte_col=sm.getColumnNumber(p)
                # Slang columns count UTF-8 bytes; expose Unicode character columns.
                source_line=lines[line-1] if line<=len(lines) else ''
                col=len(source_line.encode()[:byte_col-1].decode('utf-8'))+1
                return (line,col)
            def visit(n,module=None):
                if not isinstance(n,pyslang.syntax.SyntaxNode):return
                if n.kind==pyslang.syntax.SyntaxKind.ModuleDeclaration:
                    module=n.header.name.valueText
                    add('module',module,loc(n.sourceRange.start),loc(n.sourceRange.end),module=module)
                if n.kind==pyslang.syntax.SyntaxKind.HierarchyInstantiation:
                    for instance in n.instances:
                        if hasattr(instance,'decl'):
                            add('instance',instance.decl.name.valueText,loc(n.sourceRange.start),loc(n.sourceRange.end),module=module,cell_type=n.type.valueText)
                for child in n:visit(child,module)
            visit(tree.root);diagnostics=[dict(code=str(d.code),position=list(loc(d.location))) for d in tree.diagnostics]
        return dict(complete=not diagnostics,blocks=blocks,diagnostics=diagnostics)
    except ImportError:
        return dict(complete=False,blocks=[],diagnostics=['Parser dependency missing; install verifier source-reader requirements or use read_lines/search_text.'])
    except Exception as exc:
        return dict(complete=False,blocks=[],diagnostics=[f'{type(exc).__name__}: {str(exc)[:300]}; use read_lines/search_text.'])

if __name__=='__main__':
    print(json.dumps(worker(sys.argv[1],sys.stdin.read()),ensure_ascii=False))
