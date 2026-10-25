"""Read-only catalogue and evidence checks with caller-supplied authorized inputs.
Returned reports do not contain original requirement text.
"""
from pathlib import Path
import collections, hashlib, json, zipfile
def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
def pointer(document,path):
    assert path.startswith("/")
    value=document
    for token in path[1:].split("/"):
        token=token.replace("~1","/").replace("~0","~")
        value=value[int(token)] if isinstance(value,list) else value[token]
    return value
def catalogue_match(snapshot,index,catalogue_bytes):
    expected_ids=set(index["contracts"]);assert len(expected_ids)==396 and len(snapshot["rows"])==396
    observed={}
    def collect(value):
        if isinstance(value,dict):
            if value.get("id") in expected_ids:
                rid=value["id"];assert rid not in observed,("duplicate original ID",rid);observed[rid]=value
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    assert set(snapshot["catalogues"])==set(catalogue_bytes)
    for name,raw in catalogue_bytes.items():
        assert sha(raw)==snapshot["catalogues"][name],("catalogue digest",name)
        collect(json.loads(raw))
    assert set(observed)==expected_ids
    clauses={};snapshot_edges=set();original_edges=set();rows={}
    for row in snapshot["rows"]:
        rid=row["id"];assert rid not in rows;rows[rid]=row;original=observed[rid]
        assert original==row["original_record"],("original record content",rid)
        digest=sha(canonical(original))
        assert digest==row["source_record_sha256"]==index["contracts"][rid]["source_record_sha256"]
        for clause in row["criteria"]:
            cid=clause["id"];owner,suffix=cid.split("#",1);assert owner==rid and cid not in clauses
            text=pointer(original,suffix);assert isinstance(text,str) and text==clause["text"]
            assert sha(text.encode("utf-8"))==clause["sha256"]==index["criteria"][cid];clauses[cid]=clause["sha256"]
        for edge in row["normative_links"]:snapshot_edges.add((edge["case_id"],edge["requirement_id"]))
        if rid.startswith(("ACC-","A05-")):
            links=original.get("requirement_ids",[])
            if "requirement_id" in original:links=[*links,original["requirement_id"]]
            assert isinstance(links,list) and all(isinstance(x,str) for x in links)
            original_edges.update((rid,req) for req in links)
        else:
            links=original.get("acceptance_ids",[]);assert isinstance(links,list) and all(isinstance(x,str) for x in links)
            original_edges.update((case,rid) for case in links)
    indexed_edges={(e["case_id"],e["requirement_id"]) for e in index["normative_edges"]}
    assert set(rows)==expected_ids and clauses==index["criteria"]
    assert snapshot_edges==original_edges==indexed_edges and len(indexed_edges)==506
    families=dict(collections.Counter("CR" if r.startswith("CR-") else "MW05" if r.startswith("MW05-") else "ACC" if r.startswith("ACC-") else "A05" for r in rows))
    assert families=={"CR":208,"MW05":64,"ACC":60,"A05":64} and len(clauses)==1652
    return {"records":396,"families":families,"clauses":1652,"normative_edges":506,"all_original_record_digests_match":True,"all_clause_literal_utf8_digests_match":True,"all_original_edges_match":True,"raw_catalogue_digests_verified":sorted(snapshot["catalogues"].values()),"criteria_map_sha256":sha(canonical(clauses)),"edge_set_sha256":sha(canonical(sorted(indexed_edges))),"new_product_executions":0}
def verify_artifact_bindings(records,root):
    root=Path(root).resolve();files={};members={};bindings=0
    for record in records:
        for artifact in record["artifacts"]:
            bindings+=1;rel=artifact["path"];path=(root/rel).resolve()
            assert path.is_relative_to(root) and not Path(rel).is_absolute()
            if rel not in files:
                data=path.read_bytes();files[rel]={"bytes":len(data),"sha256":sha(data)}
            assert files[rel]["sha256"]==artifact["sha256"],(record["id"],rel)
            member=artifact.get("member")
            if member is not None:
                key=(rel,member)
                if key not in members:
                    with zipfile.ZipFile(path) as archive:
                        assert len(archive.namelist())==len(set(archive.namelist()));data=archive.read(member)
                    members[key]={"bytes":len(data),"sha256":sha(data)}
                assert members[key]["sha256"]==artifact["member_sha256"],(record["id"],key)
    return {"record_count":len(records),"artifact_bindings":bindings,"unique_containers_or_files":len(files),"unique_archive_members":len(members),"files":files,"member_bindings_sha256":sha(canonical([[a,b,v] for (a,b),v in sorted(members.items())])),"all_bindings_verified":True,"new_product_executions":0}
