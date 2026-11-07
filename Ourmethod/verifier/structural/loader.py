"""Same importer/classification as the copied DB, caching immutable Liberty facts."""
from hashlib import sha256
from pathlib import Path
import time

from .cell_catalog import CellCatalog
from .library_facts import library_metadata
from .netlist_db import NetlistDB


def load_design(runner, path, libraries, top, cache):
    deadline=time.monotonic()+runner.timeout
    parsed=runner.load_netlist(path,libraries,top)
    if parsed.status!="ok":return parsed
    result=NetlistDB.from_json(parsed.data["document"],parsed.data["identity"]["top"],evidence=parsed.evidence,deadline=deadline)
    if result.status!="ok":return result
    key=("library_metadata",tuple(sha256(Path(p).read_bytes()).hexdigest() for p in libraries))
    if key not in cache:
        metadata={}
        for path in libraries:
            for name,record in library_metadata(Path(path).read_text()).items():
                if name in metadata and metadata[name]!=record:
                    record={**record,"role":"unknown","unknowns":["Conflicting library definitions"]}
                metadata[name]=record
        cache[key]=metadata
    metadata=cache[key]
    result.data.index["library"]=metadata
    roles={"ordinary_ff":"DFF","scan_ff":"SFF","icg":"ICG","latch":"latch","combinational":"combinational"}
    result.data.catalog=CellCatalog({name:{"role":roles[r["role"]]} for name,r in metadata.items() if r["role"] in roles})
    return result
