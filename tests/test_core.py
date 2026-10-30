"""台割工房の中身（ファイルの追従・引っ越し・操作・書き出し）の試験"""
import json
import os
import shutil
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image, ImageDraw


def settle(p, rounds=3):
    """見張り役と指紋づくりの代わりに、最後まで回す"""
    for _ in range(rounds):
        listing, ql = p.scan()
        p.reconcile(listing, ql)
        for rel, st in p.index_todo():
            p.store_print(rel, p.compute_print(rel, st))


def open_proj(dk, folder):
    p = dk.Project(folder)
    p.open()
    settle(p)
    return p


def by_raw(p, name):
    return next(x for x in p.doc["pages"] if x["raw"] == name)


def raws(p):
    return [x["raw"] for x in p.doc["pages"]]


# ───────── 新しいフォルダ ─────────

def test_new_folder_builds_pages_and_pairs(dk, sample):
    p = open_proj(dk, sample)
    assert (sample / "台割.daiwari").exists()
    assert len(p.doc["pages"]) == 12
    assert raws(p) == [f"{n:03d}.png" for n in range(1, 13)]
    assert by_raw(p, "001.png")["fin"] == "台詞入り/001.png"
    assert by_raw(p, "004.png")["fin"] == "004_V2.png"
    assert by_raw(p, "005.png")["fin"] == "005_s.png"
    assert by_raw(p, "006.png")["fin"] is None
    assert by_raw(p, "001.png")["status"] == "text" and by_raw(p, "006.png")["status"] == "todo"
    st = p.state()
    qs = {q["q"] for q in st["quarantine"]}
    assert {"未使用/候補1.png", "_除外/ボツ1.png"} <= qs
    assert any(n["id"] == "legacy_q" for n in st["notices"])
    data = json.loads((sample / "台割.daiwari").read_text(encoding="utf-8"))
    assert data["app"] == "台割工房" and data["version"] == 3
    assert all(x["pixel"] for x in data["prints"].values()) or p.dirty


def test_reopen_reads_saved_order(dk, sample):
    p = open_proj(dk, sample)
    p.run("move", {"ids": [p.doc["pages"][-1]["id"]], "how": "top"})
    p.run("text", {"id": p.doc["pages"][0]["id"], "field": "lines", "value": "サキ「あのさ」"})
    p.save()
    q = open_proj(dk, sample)
    assert q.doc["pages"][0]["raw"] == "012.png"
    assert q.doc["pages"][0]["lines"] == "サキ「あのさ」"


# ───────── 引っ越し（v2 → v3） ─────────

def test_migration_from_v2(dk, sample_v2):
    p = open_proj(dk, sample_v2)
    assert not (sample_v2 / ".daiwari.json").exists()
    assert list((sample_v2 / ".daiwari" / "backup").glob("v2の台割_*.json"))
    r = raws(p)
    assert r[:6] == ["001.png", "002.png", "003.png", "004.png", "010.png", "006.png"]
    assert "017.png" not in r
    assert any(e["page"]["raw"] == "017.png" for e in p.doc["excluded"])
    blanks = [x for x in p.doc["pages"] if not x["raw"] and not x["fin"]]
    assert len(blanks) == 1 and "引きの絵" in blanks[0]["lines"]
    assert by_raw(p, "010.png")["fin"] == "010_s.png"
    assert by_raw(p, "001.png")["fin"] == "台詞入り/001.png"
    assert by_raw(p, "009.png")["fin"] == "009_V3.png" and by_raw(p, "009.png")["old"] == ["009_V2.png"]
    assert p.doc["title"] == "放課後の屋上（試験用）"
    # メモはまずセリフ欄へ。振り分けの案を1回だけ出す
    first = by_raw(p, "001.png")
    assert "「" in first["lines"] and first["memo"] == ""
    st = p.state()
    assert any(n["id"] == "memo_split" for n in st["notices"])
    p.run("notice", {"id": "memo_split", "action": "accept"})
    first = by_raw(p, "001.png")
    assert all("「" in ln for ln in first["lines"].splitlines())
    assert first["memo"] and "「" not in first["memo"]
    assert not any(n["id"] == "memo_split" for n in p.state()["notices"])
    p.run("undo", {})
    assert any(n["id"] == "memo_split" for n in p.state()["notices"])


def test_legacy_quarantine_merge_and_undo(dk, sample_v2):
    p = open_proj(dk, sample_v2)
    p.run("notice", {"id": "legacy_q", "action": "accept"})
    assert not (sample_v2 / "_除外").exists()
    assert (sample_v2 / "未使用" / "ボツ1.png").exists()
    p.run("undo", {})
    assert (sample_v2 / "_除外" / "ボツ1.png").exists()
    assert not (sample_v2 / "未使用" / "ボツ1.png").exists()


@pytest.mark.parametrize("data", [
    {"pages": {"a.png": {"status": "完成", "memo": "x"}, "b.png": {}}, "order": ["b.png", "a.png"]},
    ["a.png", "b.png"],
    {"pages": [{"files": ["a.png", "a_s.png"], "state": 3}, {"type": "blank", "note": "空き"}, {"name": "b.png"}]},
    {"items": [{"raw": "a.png", "file": "a_s.png", "status": "fix"}], "hidden": [{"file": "c.png"}]},
])
def test_parse_legacy_variants(dk, data):
    info = dk.parse_legacy(data)
    files = [x["raw"] for x in info["pages"] if x["raw"]]
    assert "a.png" in files
    if isinstance(data, dict) and data.get("order"):
        assert files == ["b.png", "a.png"]
        assert info["pages"][1]["status"] == "done"
    if isinstance(data, dict) and "items" in data:
        assert info["pages"][0]["fin"] == "a_s.png" and info["pages"][0]["status"] == "fix"
        assert info["excluded"] == ["c.png"]


# ───────── ファイルの変化への追従 ─────────

def test_rename_only(dk, sample):
    p = open_proj(dk, sample)
    p.run("text", {"id": by_raw(p, "003.png")["id"], "field": "lines", "value": "三ページ目"})
    os.rename(sample / "003.png", sample / "改名_003.png")
    settle(p)
    assert len(p.doc["pages"]) == 12
    pg = by_raw(p, "改名_003.png")
    assert pg["lines"] == "三ページ目" and pg["fin"] == "台詞入り/003.png"
    assert p.doc["pages"].index(pg) == 2


def test_rename_and_strip_metadata_like_banzuke(dk, sample):
    """番付工房でリネームとメタデータ削除を同時にしても、画素の指紋で追える"""
    p = open_proj(dk, sample)
    for i, pg in enumerate(p.doc["pages"]):
        p.run("text", {"id": pg["id"], "field": "memo", "value": f"memo{i}"})
    for n in range(1, 13):
        src = sample / f"{n:03d}.png"
        data = dk.strip_png(src.read_bytes())
        assert b"tEXt" not in data and len(data) < src.stat().st_size
        (sample / f"作品A_{n:03d}.png").write_bytes(data)
        src.unlink()
    settle(p)
    assert len(p.doc["pages"]) == 12
    assert raws(p) == [f"作品A_{n:03d}.png" for n in range(1, 13)]
    assert [pg["memo"] for pg in p.doc["pages"]] == [f"memo{i}" for i in range(12)]
    assert by_raw(p, "作品A_001.png")["fin"] == "台詞入り/001.png"


def test_names_swapped(dk, sample):
    p = open_proj(dk, sample)
    p.run("text", {"id": by_raw(p, "007.png")["id"], "field": "lines", "value": "P7"})
    p.run("text", {"id": by_raw(p, "008.png")["id"], "field": "lines", "value": "P8"})
    os.rename(sample / "007.png", sample / "tmp.png")
    os.rename(sample / "008.png", sample / "007.png")
    os.rename(sample / "tmp.png", sample / "008.png")
    settle(p)
    assert len(p.doc["pages"]) == 12
    assert by_raw(p, "008.png")["lines"] == "P7"
    assert by_raw(p, "007.png")["lines"] == "P8"


def test_overwrite_in_place(dk, sample):
    p = open_proj(dk, sample)
    pg = by_raw(p, "003.png")
    v_before = p.state()["pages"][2]["fin_v"]
    path = sample / "台詞入り" / "003.png"
    im = Image.open(path).convert("RGB")
    ImageDraw.Draw(im).rectangle((10, 10, 60, 60), fill=(255, 0, 0))
    time.sleep(0.01)
    im.save(path)
    settle(p)
    assert len(p.doc["pages"]) == 12
    assert by_raw(p, "003.png") is pg and pg["fin"] == "台詞入り/003.png"
    assert p.state()["pages"][2]["fin_v"] != v_before


def test_findir_save_becomes_fin(dk, sample):
    p = open_proj(dk, sample)
    shutil.copy(sample / "008.png", sample / "台詞入り" / "008.png")
    settle(p)
    pg = by_raw(p, "008.png")
    assert pg["fin"] == "台詞入り/008.png" and pg["status"] == "text" and pg["new"]
    assert len(p.doc["pages"]) == 12


def test_v3_moves_v2_to_old(dk, sample):
    p = open_proj(dk, sample)
    shutil.copy(sample / "004_V2.png", sample / "004_V3.png")
    settle(p)
    pg = by_raw(p, "004.png")
    assert pg["fin"] == "004_V3.png" and pg["old"] == ["004_V2.png"]
    assert len(p.doc["pages"]) == 12


def test_new_file_goes_to_waiting_blank(dk, sample):
    p = open_proj(dk, sample)
    res = p.run("blank", {"ids": [p.doc["pages"][1]["id"]], "where": "after", "wait": True})
    bid = res["select"][0]
    assert p.doc["pages"][2]["id"] == bid and p.doc["pages"][2]["wait"]
    shutil.copy(sample / "_除外" / "ボツ1.png", sample / "新しい絵.png")
    settle(p)
    assert p.doc["pages"][2]["raw"] == "新しい絵.png" and not p.doc["pages"][2]["wait"]
    shutil.copy(sample / "_除外" / "ボツ2.png", sample / "もう1枚.png")
    settle(p)
    assert p.doc["pages"][-1]["raw"] == "もう1枚.png" and p.doc["pages"][-1]["new"]


def test_missing_file_keeps_page_and_text(dk, sample, tmp_path):
    p = open_proj(dk, sample)
    p.run("text", {"id": by_raw(p, "007.png")["id"], "field": "lines", "value": "大事なセリフ"})
    shutil.move(str(sample / "007.png"), str(tmp_path / "outside.png"))
    settle(p)
    st = p.state()
    pg = next(x for x in st["pages"] if x["raw"] == "007.png")
    assert pg["kind"] == "missing" and pg["lines"] == "大事なセリフ"
    shutil.move(str(tmp_path / "outside.png"), str(sample / "007.png"))
    settle(p)
    pg = next(x for x in p.state()["pages"] if x["raw"] == "007.png")
    assert pg["kind"] == "image"


def test_similar_picture_is_suggested(dk, sample):
    p = open_proj(dk, sample)
    Image.open(sample / "008.png").convert("RGB").save(sample / "描き直し.jpg", quality=92)
    (sample / "008.png").unlink()
    settle(p)
    st = p.state()
    assert st["suggestions"], "似た絵の確認が出ていない"
    s = st["suggestions"][0]
    assert s["missing"] == "008.png" and s["cand"] == "描き直し.jpg"
    p.run("suggest", {"id": s["id"], "accept": True})
    assert len(p.doc["pages"]) == 12
    assert p.doc["pages"][7]["raw"] == "描き直し.jpg"


def test_moved_into_unused_by_hand_and_back(dk, sample):
    p = open_proj(dk, sample)
    p.run("text", {"id": by_raw(p, "006.png")["id"], "field": "lines", "value": "六"})
    os.rename(sample / "006.png", sample / "未使用" / "006.png")
    settle(p)
    assert "006.png" not in raws(p) and len(p.doc["pages"]) == 11
    assert any(e["moves"][0][1] == "未使用/006.png" for e in p.doc["quarantined"])
    os.rename(sample / "未使用" / "006.png", sample / "006.png")
    settle(p)
    assert raws(p)[5] == "006.png" and by_raw(p, "006.png")["lines"] == "六"
    assert not p.doc["quarantined"]


# ───────── 操作と元に戻す ─────────

def test_quarantine_undo_redo_and_restore(dk, sample):
    p = open_proj(dk, sample)
    pid = by_raw(p, "005.png")["id"]
    p.run("text", {"id": pid, "field": "lines", "value": "五"})
    p.run("quarantine", {"ids": [pid]})
    assert (sample / "未使用" / "005.png").exists() and (sample / "未使用" / "005_s.png").exists()
    assert not (sample / "005.png").exists() and len(p.doc["pages"]) == 11
    p.run("undo", {})
    assert (sample / "005.png").exists() and (sample / "005_s.png").exists()
    assert raws(p)[4] == "005.png" and by_raw(p, "005.png")["lines"] == "五"
    p.run("redo", {})
    assert not (sample / "005.png").exists()
    settle(p)
    p.run("unquarantine", {"items": ["未使用/005.png"]})
    assert raws(p)[4] == "005.png" and by_raw(p, "005.png")["fin"] == "005_s.png"
    assert by_raw(p, "005.png")["lines"] == "五"
    settle(p)
    assert len(p.doc["pages"]) == 12


def test_quarantine_name_collision(dk, sample):
    p = open_proj(dk, sample)
    shutil.copy(sample / "001.png", sample / "未使用" / "002.png")
    settle(p)
    p.run("quarantine", {"ids": [by_raw(p, "002.png")["id"]]})
    assert (sample / "未使用" / "002_2.png").exists()


def test_exclude_and_restore(dk, sample):
    p = open_proj(dk, sample)
    pid = by_raw(p, "009.png")["id"]
    p.run("exclude", {"ids": [pid]})
    assert "009.png" not in raws(p) and (sample / "009.png").exists()
    settle(p)
    assert "009.png" not in raws(p)
    eid = p.doc["excluded"][0]["id"]
    p.run("unexclude", {"ids": [eid]})
    assert raws(p)[8] == "009.png"


def test_moves(dk, sample):
    p = open_proj(dk, sample)
    ids = [x["id"] for x in p.doc["pages"]]
    p.run("move", {"ids": [ids[3], ids[4]], "how": "top"})
    assert [x["id"] for x in p.doc["pages"]][:3] == [ids[3], ids[4], ids[0]]
    p.run("move", {"ids": [ids[3]], "how": "end"})
    assert p.doc["pages"][-1]["id"] == ids[3]
    p.run("move", {"ids": [ids[0]], "how": "number", "n": 5})
    assert p.doc["pages"][4]["id"] == ids[0]
    p.run("move", {"ids": [ids[0]], "how": "up", "k": 2})
    assert p.doc["pages"][2]["id"] == ids[0]
    p.run("move", {"ids": [ids[0]], "how": "before", "before": ids[10]})
    order = [x["id"] for x in p.doc["pages"]]
    assert order.index(ids[0]) == order.index(ids[10]) - 1
    for _ in range(5):
        p.run("undo", {})
    assert [x["id"] for x in p.doc["pages"]] == ids


def test_text_edits_coalesce_into_one_undo(dk, sample):
    p = open_proj(dk, sample)
    pid = p.doc["pages"][0]["id"]
    n0 = len(p.undo_stack)
    for v in ("サ", "サキ", "サキ「", "サキ「あ」"):
        p.run("text", {"id": pid, "field": "lines", "value": v})
    assert len(p.undo_stack) == n0 + 1
    p.run("undo", {})
    assert p.doc["pages"][0]["lines"] == ""


def test_blank_fill_pair_split(dk, sample):
    p = open_proj(dk, sample)
    b = p.run("blank", {"ids": [p.doc["pages"][0]["id"]], "where": "after"})["select"][0]
    p.run("text", {"id": b, "field": "lines", "value": "空白に書いたセリフ"})
    src = by_raw(p, "012.png")["id"]
    p.run("fill", {"blank": b, "src": src})
    pg = p.doc["pages"][1]
    assert pg["id"] == b and pg["raw"] == "012.png" and pg["lines"] == "空白に書いたセリフ"
    assert len(p.doc["pages"]) == 12
    pid = by_raw(p, "001.png")["id"]
    q = p.run("split", {"id": pid, "role": "fin"})["select"][0]
    assert p.doc["pages"][1]["id"] == q and p.doc["pages"][1]["fin"] == "台詞入り/001.png"
    p.run("pair", {"ids": [pid, q]})
    assert by_raw(p, "001.png")["fin"] == "台詞入り/001.png" and len(p.doc["pages"]) == 12
    with pytest.raises(dk.OpError):
        p.run("delete", {"ids": [pid]})


# ───────── 書き出し・バックアップ ─────────

def test_export_copies_and_strips(dk, sample):
    p = open_proj(dk, sample)
    p.run("blank", {"ids": [p.doc["pages"][0]["id"]], "where": "after"})
    res = p.export({"prefix": "テスト_", "pad": 3, "strip": True, "blank": "skip", "use": "fin"})
    dest = sample.__class__(res["dest"])
    files = sorted(x.name for x in dest.iterdir())
    assert "台本.txt" in files and res["count"] == 12 and res["skipped"] == 1
    assert "テスト_001.png" in files and "テスト_002.png" not in files and "テスト_003.png" in files
    out = Image.open(dest / "テスト_003.png")
    assert not out.text
    src = Image.open(sample / "台詞入り" / "002.png")      # 完成を優先して書き出す
    assert out.tobytes() == src.tobytes()
    assert (sample / "002.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "tEXt" in (sample / "002.png").read_bytes().decode("latin-1")
    script = (dest / "台本.txt").read_text(encoding="utf-8-sig")
    assert "P.001" in script and "空白" in script


def test_backups_rotate_and_restore(dk, sample, monkeypatch):
    p = open_proj(dk, sample)
    monkeypatch.setattr(dk, "BACKUP_EVERY", 0)
    for i in range(3):
        p.run("title", {"value": f"題{i}"})
        time.sleep(1.05)
        p.save()
    items = p.list_backups()
    assert any(b["kind"] == "日ごと" for b in items)
    assert sum(1 for b in items if b["kind"] == "直近") >= 2
    oldest = [b for b in items if b["kind"] == "直近"][-1]
    p.run("restore_backup", {"name": oldest["name"]})
    assert p.doc["title"] != "題2"
    p.run("undo", {})
    assert p.doc["title"] == "題2"


def test_broken_project_file_falls_back_to_backup(dk, sample):
    p = open_proj(dk, sample)
    p.run("title", {"value": "保存した題"})
    p.save()
    shutil.copy(sample / "台割.daiwari", sample / ".daiwari" / "backup" / "直近_20990101-000000.json")
    (sample / "台割.daiwari").write_text("{ 壊れた", encoding="utf-8")
    q = open_proj(dk, sample)
    assert q.doc["title"] == "保存した題"
    assert any("バックアップ" in m["text"] for m in q.messages)


# ───────── 速さ ─────────

def test_second_open_does_not_read_images(dk, tmp_path, monkeypatch):
    import make_sample
    monkeypatch.setenv("DAIWARI_APPDATA", str(tmp_path / "appdata"))
    folder = tmp_path / "大きい作品"
    make_sample.make(folder, 200, v2=True, size=(96, 140))
    p = open_proj(dk, folder)
    assert len(p.doc["pages"]) >= 199
    p.save()
    calls = []
    real = dk.decode_image
    monkeypatch.setattr(dk, "decode_image", lambda path: calls.append(path) or real(path))
    t0 = time.perf_counter()
    q = dk.Project(folder)
    q.open()
    listing, ql = q.scan()
    q.reconcile(listing, ql)
    st = q.state()
    dt_ = time.perf_counter() - t0
    assert not calls, f"2回目なのに絵を開いた: {calls[:3]}"
    assert dt_ < 2.0 and len(st["pages"]) == len(p.doc["pages"])


# ───────── 画面とのやり取り（HTTP） ─────────

def _get(url, headers=None, host=None):
    req = urllib.request.Request(url, headers=headers or {})
    if host:
        req.add_unredirected_header("Host", host)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_requires_token_and_local_host(dk, sample):
    app = dk.App(sample)
    app.start()
    try:
        base = f"http://127.0.0.1:{app.port}"
        assert _get(base + "/api/state")[0] == 403
        code, body = _get(base + "/api/state", {"X-Token": app.token})
        assert code == 200 and json.loads(body)["state"]["pages"]
        assert _get(base + "/api/state", {"X-Token": app.token}, host="evil.example:80")[0] == 403
        assert _get(base + f"/img?f=../secret.png&t={app.token}")[0] in (403, 404)
        assert _get(base + f"/img?f=C:/Windows/win.ini&t={app.token}")[0] in (403, 404)
        code, body = _get(base + f"/thumb?f=001.png&t={app.token}")
        assert code == 200 and body[:2] == b"\xff\xd8"
        code, body = _get(base + f"/?t={app.token}")
        assert code == 200 and app.token.encode() in body
        req = urllib.request.Request(base + "/api/op", data=json.dumps(
            {"op": "status", "args": {"ids": [app.proj.doc["pages"][0]["id"]], "status": "done"}}).encode(),
            headers={"X-Token": app.token, "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=5) as r:
            out = json.loads(r.read())
        assert out["ok"] and out["state"]["pages"][0]["status"] == "done"
        assert dk.find_running(app.proj.folder)
    finally:
        app.shutdown()
    assert not (sample / ".daiwari" / "instance.json").exists()


def test_locked_file_is_not_mistaken_for_new_page(dk, sample, monkeypatch):
    """書き込み中で一時的に開けない絵は、すぐ新しいページにせず、開けるようになってから追う"""
    p = open_proj(dk, sample)
    data = dk.strip_png((sample / "010.png").read_bytes())
    (sample / "改名_010.png").write_bytes(data)
    (sample / "010.png").unlink()
    real = dk.decode_image
    locked = {"on": True}

    def fake(path):
        if locked["on"] and path.name == "改名_010.png":
            raise PermissionError("使用中")
        return real(path)

    monkeypatch.setattr(dk, "decode_image", fake)
    listing, ql = p.scan()
    p.reconcile(listing, ql)
    assert "改名_010.png" not in raws(p) and len(p.doc["pages"]) == 12
    assert p.need_reconcile
    locked["on"] = False
    settle(p)
    assert raws(p)[9] == "改名_010.png" and len(p.doc["pages"]) == 12


def test_unreadable_legacy_file_is_kept(dk, sample):
    (sample / ".daiwari.json").write_text("{ これは壊れている", encoding="utf-8")
    p = open_proj(dk, sample)
    assert len(p.doc["pages"]) == 12
    assert list((sample / ".daiwari" / "backup").glob("v2の台割_*.json"))
    assert any("読めなかった" in m["text"] for m in p.messages)


def test_relay_template_is_valid_python(dk):
    src = dk.RELAY_TEMPLATE.format(pyw=repr(r"C:\Python314\pythonw.exe"), app=repr(r"C:\ツール\台割工房\台割工房.pyw"))
    compile(src, "daiwari.py", "exec")
    assert 'rstrip("\\\\/")' in src


def test_metadata_strip_jpeg_keeps_orientation(dk, tmp_path):
    from PIL import Image
    im = Image.new("RGB", (40, 20), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x010E] = "生成情報のつもり"
    im.save(tmp_path / "a.jpg", exif=exif.tobytes(), comment=b"prompt: test")
    raw = (tmp_path / "a.jpg").read_bytes()
    out = dk.strip_jpeg(raw)
    (tmp_path / "b.jpg").write_bytes(out)
    b = Image.open(tmp_path / "b.jpg")
    assert b.getexif().get(0x0112) == 6 and 0x010E not in b.getexif()
    assert b"prompt" not in out
    assert b.tobytes() == Image.open(tmp_path / "a.jpg").tobytes()


def test_200_pages_full_scenario(dk, tmp_path, monkeypatch):
    """実物に近い200ページ（原画と完成の混在・空白・v2 の管理データつき）で、7つの試験を続けて行う"""
    import make_sample
    monkeypatch.setenv("DAIWARI_APPDATA", str(tmp_path / "appdata"))
    folder = tmp_path / "二百ページ"
    make_sample.make(folder, 200, v2=True, size=(120, 175))
    # 1) 引っ越し
    p = open_proj(dk, folder)
    n0 = len(p.doc["pages"])
    assert n0 == 201 and sum(1 for x in p.doc["pages"] if not x["raw"] and not x["fin"]) == 2
    assert by_raw(p, "060.png")["fin"] == "台詞入り/060.png"
    assert any(e["page"].get("fin") == "台詞入り/017.png" for e in p.doc["excluded"])   # 除外したページの完成は除外側へ
    texts = {x["id"]: x["lines"] for x in p.doc["pages"]}
    # 2) リネーム ＋ 3) メタデータ削除（番付工房で全部まとめて）
    for x in list(p.doc["pages"]):
        if x["raw"] and "/" not in x["raw"]:
            src = folder / x["raw"]
            (folder / f"本番_{x['raw']}").write_bytes(dk.strip_png(src.read_bytes()))
            src.unlink()
    settle(p)
    assert len(p.doc["pages"]) == n0
    assert all(x["raw"].startswith("本番_") for x in p.doc["pages"] if x["raw"])
    assert {x["id"]: x["lines"] for x in p.doc["pages"]} == texts
    # 4) 上書き
    pg = by_raw(p, "本番_005.png")
    im = Image.open(folder / pg["fin"]).convert("RGB")
    ImageDraw.Draw(im).ellipse((5, 5, 40, 40), fill=(0, 0, 255))
    im.save(folder / pg["fin"])
    # 5) 台詞入りへの保存（名前が変わったあとの原画の名前で）
    shutil.copy(folder / "本番_150.png", folder / "台詞入り" / "本番_150.png")
    settle(p)
    assert len(p.doc["pages"]) == n0
    assert by_raw(p, "本番_150.png")["fin"] == "台詞入り/本番_150.png"
    # 6) 待ち受け
    bid = p.run("blank", {"ids": [p.doc["pages"][99]["id"]], "where": "after", "wait": True})["select"][0]
    shutil.copy(folder / "_除外" / "ボツ1.png", folder / "生成した絵.png")
    settle(p)
    assert next(x for x in p.doc["pages"] if x["id"] == bid)["raw"] == "生成した絵.png"
    assert p.doc["pages"][100]["id"] == bid
    # 7) 元に戻す（隔離 → 戻す で、ファイルもページも元どおり）
    ids = [x["id"] for x in p.doc["pages"][10:15]]
    p.run("quarantine", {"ids": ids})
    assert len(p.doc["pages"]) == n0 + 1 - 5
    p.run("undo", {})
    settle(p)
    assert [x["id"] for x in p.doc["pages"][10:15]] == ids and len(p.doc["pages"]) == n0 + 1
