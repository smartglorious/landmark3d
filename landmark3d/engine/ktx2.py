"""Embed KTX2 copies of a GLB's textures (KHR_texture_basisu) next to the PNG/JPEG they came from (plan M5, step 6).

Each texture keeps its PNG/JPEG source as the fallback, so viewers without KTX2 support still show the model. The KTX2
copy is added as an extra image and referenced by the texture's KHR_texture_basisu extension. Normal maps are encoded
with UASTC (they lose too much detail in ETC1S); colour maps use ETC1S (much smaller).
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

MAGIC = 0x46546C67            # "glTF"
JSON_T = 0x4E4F534A
BIN_T = 0x004E4942
EXT = "KHR_texture_basisu"
FALLBACK_MIMES = ("image/png", "image/jpeg")


def read_glb(data: bytes) -> tuple[dict, bytes]:
    if len(data) < 12 or struct.unpack_from("<I", data, 0)[0] != MAGIC:
        raise ValueError("not a GLB file")
    total = min(struct.unpack_from("<I", data, 8)[0], len(data))
    off, doc, binc = 12, None, b""
    while off + 8 <= total:
        ln, typ = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + ln]
        off += 8 + ln
        if typ == JSON_T:
            doc = json.loads(chunk.decode("utf-8").rstrip(" \x00"))
        elif typ == BIN_T:
            binc = chunk
    if doc is None:
        raise ValueError("GLB has no JSON chunk")
    return doc, binc


def write_glb(doc: dict, binc: bytes) -> bytes:
    jb = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    jb += b" " * ((4 - len(jb) % 4) % 4)
    body = struct.pack("<II", len(jb), JSON_T) + jb
    if binc:
        binc += b"\x00" * ((4 - len(binc) % 4) % 4)
        body += struct.pack("<II", len(binc), BIN_T) + binc
    return struct.pack("<III", MAGIC, 2, 12 + len(body)) + body


def _image_bytes(doc: dict, binc: bytes, image: dict) -> bytes:
    bv = doc["bufferViews"][image["bufferView"]]
    start = bv.get("byteOffset", 0)
    return binc[start: start + bv["byteLength"]]


def toktx_encoder(toktx: str, timeout: int = 300):
    """Encoder that runs the KTX-Software toktx tool. Returns bytes of a KTX2 file."""
    def encode(data: bytes, mime: str, normal: bool) -> bytes:
        suffix = ".png" if mime == "image/png" else ".jpg"
        with tempfile.TemporaryDirectory(prefix="l3d_ktx_") as tmp:
            src = Path(tmp) / f"in{suffix}"
            dst = Path(tmp) / "out.ktx2"
            src.write_bytes(data)
            # toktx ignores unknown encoder names without an error, so the names must be exact: "uastc" and "etc1s"
            enc = ["--encode", "uastc", "--uastc_quality", "2"] if normal else ["--encode", "etc1s"]
            cmd = [toktx, "--t2", *enc, "--genmipmap", str(dst), str(src)]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
            if p.returncode != 0 or not dst.exists():
                raise RuntimeError(f"toktx failed ({p.returncode}): {(p.stderr or p.stdout).strip()[-300:]}")
            return dst.read_bytes()
    return encode


def embed(glb: bytes, encode) -> tuple[bytes, dict]:
    """Return (new GLB bytes, report). `encode(data, mime, normal) -> bytes` makes each KTX2 copy. A texture whose image
    cannot be encoded keeps only its PNG/JPEG, and the report names it. Nothing is half-written: the caller gets either
    the full new file or the untouched input."""
    doc, binc = read_glb(glb)
    images = doc.get("images", [])
    normal_textures = {m["normalTexture"]["index"] for m in doc.get("materials", []) if "normalTexture" in m}
    normal_images = {doc["textures"][t]["source"] for t in normal_textures if "source" in doc["textures"][t]}
    report = {"encoded": 0, "skipped": [], "images": []}
    if not images:
        return glb, {**report, "reason": "no images"}
    doc.setdefault("bufferViews", [])
    doc.setdefault("buffers", [{"byteLength": len(binc)}])
    ktx_index: dict[int, int] = {}
    new_images = []
    binc = bytearray(binc)
    for i, image in enumerate(images):
        mime = image.get("mimeType")
        if mime not in FALLBACK_MIMES or "bufferView" not in image:
            report["skipped"].append({"image": i, "reason": f"mimeType {mime} not encoded"})
            continue
        try:
            ktx = encode(_image_bytes(doc, bytes(binc), image), mime, i in normal_images)
        except Exception as exc:                                  # a failed encode keeps the fallback, and says so
            report["skipped"].append({"image": i, "reason": str(exc)[:300]})
            continue
        while len(binc) % 4:
            binc.append(0)
        bv_index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binc), "byteLength": len(ktx)})
        binc += ktx
        ktx_index[i] = len(images) + len(new_images)
        new_images.append({"bufferView": bv_index, "mimeType": "image/ktx2", "name": f"{image.get('name', i)}_ktx2"})
        report["encoded"] += 1
        report["images"].append({"image": i, "bytes_png_or_jpeg": doc["bufferViews"][image["bufferView"]]["byteLength"],
                                 "bytes_ktx2": len(ktx), "normal": i in normal_images})
    if not new_images:
        return glb, report
    doc["images"] = images + new_images
    for tex in doc.get("textures", []):
        src = tex.get("source")
        if src in ktx_index:
            tex.setdefault("extensions", {})[EXT] = {"source": ktx_index[src]}
    used = set(doc.get("extensionsUsed", []))
    used.add(EXT)
    doc["extensionsUsed"] = sorted(used)                           # not required: viewers without KTX2 use the fallback
    doc["buffers"][0]["byteLength"] = len(binc)
    return write_glb(doc, bytes(binc)), report


def embed_file(path: Path, toktx: str) -> dict:
    """Rewrite the GLB at `path` with KTX2 copies. Returns the report (also says how the file changed)."""
    path = Path(path)
    before = path.read_bytes()
    try:
        after, report = embed(before, toktx_encoder(toktx))
    except (ValueError, KeyError) as exc:
        return {"container": "fallback", "reason": f"GLB could not be read: {exc}"}
    if after is before:
        return {"container": "fallback", **report}
    tmp = path.with_suffix(".ktx2.tmp")
    tmp.write_bytes(after)
    os.replace(tmp, path)
    return {"container": "ktx2", "bytes_before": len(before), "bytes_after": len(after), **report}
