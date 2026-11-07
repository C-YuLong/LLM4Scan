"""Bounded text views with source-based, character-addressable continuation."""
import json

CHUNK_CHARS = 6000


def size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str))


def text_preview(value, cursor, limit):
    rows=value.get('rows',[])
    base={k:v for k,v in value.items() if k not in {'rows','values','violations','next_read'}}
    search=value.get('operation')=='search_text' or 'search' in value
    page=[];index=cursor;offset=value.get('char_offset',0) if cursor==0 else 0
    if search:offset=0
    end_line=value.get('line_end')

    def result(next_read, next_index, row_partial=False):
        partial=row_partial or any(r.get('partial') for r in page) or cursor>0 or bool(offset)
        omitted=bool(next_read) or next_index is not None or partial
        return dict(base,rows=list(page),total_rows=len(rows),cursor=cursor,
                    next_cursor=next_index,omitted=omitted,partial=partial,
                    display_complete=not omitted,next_read=next_read,
                    eof=bool(value.get('eof') and next_read is None and next_index is None),
                    reading_hint='Use next_read with read_lines; offsets count Unicode characters, not bytes. Log replacement lists can be queried via query_report table=log_events/log_members.')

    if search:
        while index<len(rows):
            original=rows[index];text=original['text'];hit=text.find(value.get('search',''))
            start=max(0,hit-200) if len(text)>600 else 0
            stop=min(len(text),start+600)
            row=dict(original,text=text[start:stop],line_length=len(text),char_start=start,char_end=stop,
                     partial=start>0 or stop<len(text),match_start=hit,
                     next_read=dict(artifact=value['artifact'],start=original['line'],end=original['line'],char_offset=stop) if stop<len(text) else None)
            page.append(row)
            next_index=index+1 if index+1<len(rows) else None
            candidate=result(next((r["next_read"] for r in page if r.get("next_read")),None),next_index)
            if size(candidate)>limit:
                page.pop()
                if not page:raise ValueError('Text result limit too small for search metadata and an excerpt; increase max_tool_result_chars')
                return result(next((r["next_read"] for r in page if r.get("next_read")),None),index)
            index+=1
            if len(page)>=value.get('search_page_rows',30):return result(next((r["next_read"] for r in page if r.get("next_read")),None),index if index<len(rows) else None)
        return result(next((r["next_read"] for r in page if r.get("next_read")),None),None)

    while index<len(rows):
        original=rows[index];text=original['text'];start=offset if index==cursor else 0
        if start>len(text):raise ValueError('char_offset exceeds the selected source line length')
        if start==len(text) and text:
            index+=1;offset=0;continue
        stop=min(len(text),start+CHUNK_CHARS)
        def attempt(stop):
            row=dict(original,text=text[start:stop],line_length=len(text),char_start=start,char_end=stop,
                     partial=start>0 or stop<len(text))
            page.append(row)
            if stop<len(text):
                continuation=dict(artifact=value['artifact'],start=original['line'],end=end_line or original['line'],char_offset=stop)
                next_index=None  # A row cursor cannot advance inside this long line.
            elif index+1<len(rows):
                continuation=dict(artifact=value['artifact'],start=rows[index+1]['line'],end=end_line or rows[-1]['line'],char_offset=0)
                next_index=index+1
            else:continuation=None;next_index=None
            candidate=result(continuation,next_index)
            page.pop()
            return row,candidate
        row,candidate=attempt(stop)
        if size(candidate)>limit:
            lo=start+1;hi=stop;best=None
            while lo<=hi:
                mid=(lo+hi)//2;item,view=attempt(mid)
                if size(view)<=limit:best=(item,view);lo=mid+1
                else:hi=mid-1
            if best is None:
                if page:
                    continuation=dict(artifact=value['artifact'],start=original['line'],end=end_line or rows[-1]['line'],char_offset=start)
                    return result(continuation,index if index>cursor else None)
                raise ValueError('Text result limit too small for metadata and any source character; increase max_tool_result_chars')
            row,candidate=best;stop=row['char_end']
        page.append(row)
        if stop<len(text):return candidate
        index+=1;offset=0
        # Leave room for continuation metadata instead of returning an empty row.
    return result(None,None)
