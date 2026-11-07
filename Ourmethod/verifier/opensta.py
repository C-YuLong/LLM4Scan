"""Native OpenSTA read-only Tcl bridge. No netlist parsing or query semantics here."""
import base64
from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time
import uuid

from .trace_connection import NATIVE_BRIDGE

COMMANDS='get_cells get_pins get_libs get_lib_cells get_lib_pins get_nets get_ports get_property get_full_name get_name get_fanin get_fanout report_instance report_net report_lib_cell help'.split()
MAX_OUTPUT=1024*1024
DESCRIPTION='''Use opensta_query for additional read-only native facts not covered by the fixed tools, such as library metadata or special object properties. For population/pin discovery use query_cell_connections; for a concrete pin's direct connection to a top input use trace_connection; for before/after predicates use compare_design. Supply artifact=registered .v/.sv, top=exact module, script=read-only Tcl, cursor=0. The program loads registered Liberty/netlist files. Use help for options and full object names; never guess pins or infer top sources from local nets/get_fanin. Print returned objects individually with get_full_name. Follow next_read for saved text. Errors/timeouts remain UNKNOWN, never disconnection.'''
GUIDE=DESCRIPTION+'''
Native query details: each call has fresh Tcl variables; design sessions are reused by snapshot. Library discovery example: foreach l [get_libs *] {set n [get_full_name $l]; foreach p [get_lib_pins $n/<exact_cell_type>/*] {puts [get_full_name $p]}}. Replace <exact_cell_type> with an observed type. For fixed cross-hierarchy tracing call trace_connection, rather than constructing a Tcl tracing script.
Supported native commands: '''+', '.join(COMMANDS)+'''. Tcl variables/lists/loops/conditions and puts are available; file/process/network/design modification and output redirection are unavailable. Empty results do not prove task success. Complete results do not force assessment. Guided queries remain available within global time/call/token budgets until closing; update artifact scope with set_question and assess_requirement alone when ready. FACT is not program proof.
'''

def tcl_text(text):
    return '[encoding convertfrom utf-8 [binary decode base64 {'+base64.b64encode(text.encode()).decode()+'}]]'

class OpenSTAError(Exception):
    def __init__(self,message,output=''):
        super().__init__(message);self.output=output

SERVER=r'''
proc bridge_utf8 {operation value} {
    switch -- $operation {
        encode {return [encoding convertto utf-8 $value]}
        decode {return [encoding convertfrom utf-8 $value]}
        default {error "UTF-8 conversion requires encode or decode"}
    }
}
proc bridge_invoke {cmd args} {
    # Native report_* supports file redirection; never forward that option.
    foreach arg $args {
        if {[string match {>*} $arg]} {error "Native output file redirection is disabled"}
    }
    return [uplevel #0 [linsert $args 0 $cmd]]
}
proc bridge_puts {args} {
    set newline 1
    if {[lindex $args 0] eq "-nonewline"} {set newline 0; set args [lrange $args 1 end]}
    if {[llength $args] == 2 && [lindex $args 0] in {stdout stderr}} {set args [lrange $args 1 end]}
    if {[llength $args] != 1} {error "puts accepts a string and optional stdout/stderr only"}
    if {$newline} {puts stdout [lindex $args 0]} else {puts -nonewline stdout [lindex $args 0]}
    flush stdout
}
while {[gets stdin request] >= 0} {
    set child [interp create -safe]
    foreach cmd $bridge_commands {interp alias $child $cmd {} bridge_invoke $cmd}
    interp alias $child puts {} bridge_puts
    interp alias $child _utf8 {} bridge_utf8
    interp alias $child _trace_connection {} ::sta::bridge_trace_connection
    set code [catch {interp eval $child [encoding convertfrom utf-8 [binary decode base64 $request]]} result]
    interp delete $child
    puts "\n$bridge_marker $code [binary encode base64 -maxlen 0 [encoding convertto utf-8 $result]]"
    flush stdout
}
exit
'''

class Session:
    def __init__(self,executable,netlist,libraries,top,work,deadline):
        self.process=None;self.selector=None;self.marker='OPENSTA_'+uuid.uuid4().hex
        boot='\n'.join(['read_liberty '+tcl_text(str(p)) for p in libraries]+['read_verilog '+tcl_text(str(netlist)), 'link_design '+tcl_text(top)])
        script='set bridge_marker {'+self.marker+'}\nset bridge_commands {'+' '.join(COMMANDS)+'}\n'
        script+='set code [catch {\n'+boot+'\n} result]\n'
        script+='puts "\\n$bridge_marker $code [binary encode base64 -maxlen 0 [encoding convertto utf-8 $result]]"\nflush stdout\n'+NATIVE_BRIDGE+SERVER
        path=Path(work)/('opensta_'+uuid.uuid4().hex+'.tcl');path.write_text(script)
        self.path=path
        try:
            self.process=subprocess.Popen([executable,'-no_init','-exit',str(path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,cwd=work,start_new_session=True)
            os.set_blocking(self.process.stdout.fileno(),False)
            self.selector=selectors.DefaultSelector();self.selector.register(self.process.stdout,selectors.EVENT_READ)
            self.load=self.receive(deadline)
            if self.load['error']:raise OpenSTAError('OpenSTA design load failed',self.load['output']+'\n'+self.load['result'])
        except Exception:
            self.close();raise

    def receive(self,deadline):
        data=bytearray();pattern=re.compile(rb'(?:^|\n)'+self.marker.encode()+rb' (\d+) ([A-Za-z0-9+/=]*)\r?\n')
        while True:
            left=min(15,deadline-time.monotonic())
            if left<=0:raise OpenSTAError('OpenSTA timed out; session terminated',data.decode(errors='replace'))
            events=self.selector.select(left)
            if not events:continue
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise OpenSTAError('OpenSTA process exited',data.decode(errors='replace'))
            data.extend(chunk)
            if len(data)>MAX_OUTPUT:raise OpenSTAError('OpenSTA output exceeded 1MiB; narrow query',bytes(data[:MAX_OUTPUT]).decode(errors='replace'))
            match=pattern.search(data)
            if match:
                output=bytes(data[:match.start()]).decode(errors='replace')
                result=base64.b64decode(match[2]).decode(errors='replace')
                native_errors=[line for line in output.splitlines() if re.match(r'^\s*Error[: ]',line)]
                return dict(output=output,result=result,error=bool(int(match[1]) or native_errors),
                    diagnostics=[line for line in output.splitlines() if re.match(r'^\s*(Error|Warning)[: ]',line)])

    def query(self,script,deadline):
        if self.process.poll() is not None:raise OpenSTAError('OpenSTA process exited')
        self.process.stdin.write(base64.b64encode(script.encode())+b'\n');self.process.stdin.flush()
        return self.receive(min(deadline,time.monotonic()+15))

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill();self.process.wait(timeout=5)
            for stream in (self.process.stdin,self.process.stdout):
                if stream:stream.close()
        if self.selector:self.selector.close()
        if hasattr(self,'path'):self.path.unlink(missing_ok=True)

class OpenSTAPool:
    def __init__(self,work,deadline):
        self.work=Path(work);self.deadline=deadline;self.sessions=OrderedDict();self.version=None
        self.executable=shutil.which('sta')

    def close(self):
        for session in self.sessions.values():session.close()
        self.sessions.clear()

    def query(self,key,netlist,libraries,top,script):
        started=time.monotonic()
        if not self.executable:
            return dict(status='UNKNOWN',complete=False,error_type='OpenSTAUnavailable',output='',result='',diagnostics=['OpenSTA sta executable unavailable; install the configured image or explicitly choose query_design.'])
        try:
            if self.version is None:
                p=subprocess.run([self.executable,'-version'],capture_output=True,text=True,timeout=max(.001,min(15,self.deadline-time.monotonic())))
                if p.returncode:raise OpenSTAError('Cannot query OpenSTA version')
                self.version=p.stdout.strip()
            if key not in self.sessions:
                if len(self.sessions)>=2:self.sessions.popitem(last=False)[1].close()
                self.sessions[key]=Session(self.executable,netlist,libraries,top,self.work,min(self.deadline,time.monotonic()+15))
            session=self.sessions[key];self.sessions.move_to_end(key)
            answer=session.query(script,self.deadline)
            if answer['error']:
                self.sessions.pop(key).close()
            return dict(status='UNKNOWN' if answer['error'] else 'FACT',complete=not answer['error'],
                error_type='OpenSTAQueryError' if answer['error'] else None,output=answer['output'],result=answer['result'],
                diagnostics=session.load['diagnostics']+answer['diagnostics'],load_output=session.load['output'],
                version=self.version,elapsed_seconds=round(time.monotonic()-started,3))
        except (OpenSTAError,OSError,subprocess.SubprocessError) as exc:
            if key in self.sessions:self.sessions.pop(key).close()
            return dict(status='UNKNOWN',complete=False,error_type=type(exc).__name__,output=getattr(exc,'output',''),result='',
                        diagnostics=[str(exc)],version=self.version,elapsed_seconds=round(time.monotonic()-started,3))

def preview(value,cursor,limit):
    # Character offsets address immutable saved output; no command is rerun.
    text=value['output']
    if cursor<0 or cursor>len(text):raise ValueError('OpenSTA output cursor outside saved output')
    base={k:v for k,v in value.items() if k not in {'output','result','load_output','diagnostics','parameters','next_read','partial','omitted'}}
    def page(width):
        end=min(len(text),cursor+width);more=end<len(text)
        return dict(base,rows=[dict(text=text[cursor:end],char_start=cursor,char_end=end)],output_chars=len(text),cursor=cursor,
                    omitted=more or cursor>0,partial=more or cursor>0,display_complete=not more and cursor==0,
                    next_cursor=end if more else None,next_read=dict(tool='opensta_query',arguments={**value['parameters'],'cursor':end}) if more else None)
    lo=0;hi=min(len(text)-cursor,limit);best=None
    while lo<=hi:
        mid=(lo+hi)//2;answer=page(mid)
        if len(json.dumps(answer,ensure_ascii=False))<=limit:best=answer;lo=mid+1
        else:hi=mid-1
    if best is None or (cursor<len(text) and best['rows'][0]['char_end']==cursor):raise ValueError('OpenSTA page budget too small')
    return best
