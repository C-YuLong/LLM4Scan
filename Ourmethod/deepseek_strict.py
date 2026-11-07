"""Opt-in DeepSeek Beta Chat Completions transport for direct Verify."""
import json
import time
from urllib.parse import urlsplit
import urllib.request

import telemetry


def beta_endpoint(base_url):
    url=urlsplit(base_url)
    if (url.scheme != 'https' or url.netloc != 'api.deepseek.com'
            or url.path.rstrip('/') not in {'','/beta'} or url.query or url.fragment):
        raise ValueError('deepseek_beta_strict requires https://api.deepseek.com or its /beta base URL')
    return 'https://api.deepseek.com/beta/chat/completions'


def wire_schema(schema):
    """Retain local restrictions in the original; omit unsupported wire keywords."""
    result=dict(schema)
    for keyword in ('minLength','maxLength','minItems','maxItems'):
        result.pop(keyword,None)
    for keyword in ('properties','$defs','definitions'):
        if keyword in result:result[keyword]={k:wire_schema(v) for k,v in result[keyword].items()}
    if isinstance(result.get('items'),dict):result['items']=wire_schema(result['items'])
    for keyword in ('anyOf','oneOf','allOf'):
        if keyword in result:result[keyword]=[wire_schema(v) for v in result[keyword]]
    if isinstance(result.get('type'),list):
        branches=[]
        for kind in result['type']:
            if kind=='null':
                branches.append(dict(type='null'))
            else:
                branch={**result,'type':kind}
                if 'enum' in branch:branch['enum']=[x for x in branch['enum'] if x is not None]
                branches.append(branch)
        return dict(anyOf=branches)
    if result.get('type')=='object':
        result['required']=list(result.get('properties',{}))
        result['additionalProperties']=False
    return result


def chat_messages(messages,instructions):
    result=[dict(role='system',content=instructions)]
    pending=None
    def flush():
        nonlocal pending
        if pending is not None:result.append(pending);pending=None
    for item in messages:
        kind=item.get('type')
        if kind=='reasoning':
            if pending is None:pending=dict(role='assistant',content=None)
            pending['reasoning_content']=''.join(c.get('text','') for c in item.get('content',[]))
        elif kind=='function_call':
            if pending is None:pending=dict(role='assistant',content=None)
            pending.setdefault('tool_calls',[]).append(dict(id=item['call_id'],type='function',function=dict(name=item['name'],arguments=item['arguments'])))
        elif kind=='function_call_output':
            flush()
            result.append(dict(role='tool',tool_call_id=item['call_id'],content=item['output']))
        else:
            content=item.get('content','')
            if isinstance(content,list):content=''.join(x.get('text','') for x in content if x.get('type') in {'input_text','output_text','text'})
            if item.get('role')=='assistant' and pending is not None:
                pending['content']=(pending.get('content') or '')+content
            else:
                flush()
                result.append(dict(role=item.get('role','user'),content=content))
    flush()
    return result


def respond(settings,messages,tools,instructions,timeout,max_output_tokens,tool_choice):
    deadline=time.monotonic()+timeout
    endpoint=beta_endpoint(settings.base_url)
    functions=[dict(type='function',function=dict(name=t['name'],description=t.get('description',''),strict=True,parameters=wire_schema(t['parameters']))) for t in tools]
    choice=(dict(type='function',function=dict(name=tool_choice['name'])) if isinstance(tool_choice,dict) else tool_choice)
    effort=settings.reasoning_effort
    thinking=effort!='none'
    payload=dict(model=settings.model,messages=chat_messages(messages,instructions),tools=functions,tool_choice=choice,
                 stream=False,max_tokens=max_output_tokens,thinking=dict(type='enabled' if thinking else 'disabled'))
    if thinking:payload['reasoning_effort']='high' if effort in {'auto','medium'} else effort
    request=urllib.request.Request(endpoint,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+settings.api_key,'Content-Type':'application/json'})
    with telemetry.phase('llm_verify','Verify DeepSeek Beta Strict',model_call=True,model=settings.model,
                         reasoning_effort=effort,request_chars=len(json.dumps(payload,ensure_ascii=False))) as metric:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Verify model deadline exhausted')
        with urllib.request.urlopen(request,timeout=max(0.001,remaining)) as response:
            # HTTP read timeouts apply per socket operation. Recompute the absolute
            # remainder for each chunk so a slow response cannot extend Verify.
            read=getattr(response,'read1',None)
            if read is None:
                data=json.load(response)
            else:
                chunks=[]
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('Verify model deadline exhausted')
                    socket=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                    if socket is not None:socket.settimeout(max(0.001,remaining))
                    chunk=read(65536)
                    if not chunk:break
                    chunks.append(chunk)
                data=json.loads(b''.join(chunks))
        if time.monotonic()>=deadline:raise TimeoutError('Verify model deadline exhausted')
        choices=data.get('choices',[])
        if len(choices)!=1 or choices[0].get('finish_reason') not in {'stop','tool_calls'}:
            raise RuntimeError('DeepSeek Beta returned no complete response')
        message=choices[0]['message'];output=[]
        if message.get('reasoning_content'):
            output.append(dict(type='reasoning',content=[dict(type='reasoning_text',text=message['reasoning_content'])]))
        for call in message.get('tool_calls',[]):
            if call.get('type')!='function':raise ValueError('Unsupported DeepSeek tool call')
            output.append(dict(type='function_call',call_id=call['id'],name=call['function']['name'],arguments=call['function']['arguments']))
        if message.get('content'):
            output.append(dict(type='message',role='assistant',content=[dict(type='output_text',text=message['content'])]))
        raw=data.get('usage') or {};usage=dict(raw)
        for source,target in (('prompt_tokens','input_tokens'),('completion_tokens','output_tokens')):
            if source in raw:usage[target]=raw[source]
        if 'prompt_tokens_details' in raw:usage['input_tokens_details']=raw['prompt_tokens_details']
        if 'completion_tokens_details' in raw:usage['output_tokens_details']=raw['completion_tokens_details']
        if 'input_tokens_details' not in usage and 'prompt_cache_hit_tokens' in raw:
            usage['input_tokens_details']=dict(cached_tokens=raw['prompt_cache_hit_tokens'])
        metric['usage']=usage
    completed=dict(id=data.get('id'),model=data.get('model'),output=output,usage=usage)
    telemetry.save_response(metric,completed,message.get('content') or '')
    return completed
