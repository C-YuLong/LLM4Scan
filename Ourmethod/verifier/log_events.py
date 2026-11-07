"""Conservative SCAN-7600/7601 FF-event parser; no task or netlist claims."""
import re

COUNT=re.compile(r"\[\s*SCAN-7600\]\s+There\s+(?:were|was(?:\(were\))?)\s+(\d+)\s+'(D|scan)' flip-flops that have been replaced\.\s*$")
MEMBERS=re.compile(r"\[\s*SCAN-7601\]\s+The '(D|scan)' flip-flops that have been replaced are:\s*(.*)$")
COMMAND=re.compile(r'\[\s*CMD-0034\]\s+@(\d+):\s*(.*)$')


def parse_log_events(text,table,source):
    events=[];members=[];unknown=[];issues=[];pending=None;command='';command_id='';recognized=False
    def emit(count_line,kind,declared,list_line,payload):
        event_id=f'event_{count_line or list_line}'
        raw=[] if not payload else [v.strip() for v in payload.split(',')]
        names=[v for v in raw if v and not re.search(r'\s',v)]
        complete=declared is not None and list_line is not None and len(names)==len(raw)==declared
        if not complete:
            unknown.append(list_line or count_line)
            issues.append(dict(event_id=event_id,declared_count=declared,parsed_count=len(names),reason='Missing/malformed count or member list, or count mismatch'))
        row=dict(event_id=event_id,event_kind='dff_replaced' if kind=='D' else 'sff_replaced',
                 declared_count=declared,parsed_count=len(names),command=command,command_id=command_id,
                 event_complete=complete,_source=source,_line=count_line or list_line,member_line=list_line)
        events.append(row)
        members.extend(dict(event_id=event_id,event_kind=row['event_kind'],instance=name,command=command,
                            command_id=command_id,event_complete=complete,_source=source,_line=list_line) for name in names)
    for line_number,line in enumerate(text.splitlines(),1):
        cmd=COMMAND.search(line)
        if cmd:
            if pending:emit(*pending,None,None);pending=None
            command_id,command=cmd.groups();continue
        count=COUNT.search(line)
        if count:
            recognized=True
            if pending:emit(*pending,None,None)
            pending=(line_number,count[2],int(count[1]));continue
        listed=MEMBERS.search(line)
        if listed:
            recognized=True
            if pending and pending[1]==listed[1]:
                emit(*pending,line_number,listed[2]);pending=None
            else:
                if pending:emit(*pending,None,None);pending=None
                emit(None,listed[1],None,line_number,listed[2])
        elif re.search(r'\[\s*SCAN-760[01]\]',line):
            recognized=True;unknown.append(line_number)
            issues.append(dict(line=line_number,reason='Unrecognized SCAN FF replacement event syntax'))
    if pending:emit(*pending,None,None)
    return dict(rows=events if table=='log_events' else members,recognized=recognized,unknown=unknown,issues=issues)
