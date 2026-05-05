"""LinkedIn — cvs routes.

Carved from `app.linkedin.extras`. Routes are byte-identical to
the original; the wildcard import below inherits every helper
and module alias they reference.
"""
from __future__ import annotations

from fastapi import APIRouter
from app.linkedin.extras import *  # noqa: F403,F401

router = APIRouter(prefix="/api/linkedin", tags=["linkedin-extras"])


@router.get("/cvs")
def list_cvs():
    with connect() as con:
        rows = con.execute(
            "SELECT id, cluster, filename, size_bytes, uploaded_at "
            "FROM cvs ORDER BY cluster"
        ).fetchall()
    configured = {r["cluster"] for r in rows}
    missing = [c for c in CV_CLUSTERS if c not in configured]
    return {
        "rows": [dict(r) for r in rows],
        "clusters": list(CV_CLUSTERS),
        "missing": missing,
    }


@router.post("/cvs")
async def upload_cv(cluster: str = Form(...), file: UploadFile = File(...)):
    if cluster not in CV_CLUSTERS:
        raise HTTPException(400, f"cluster must be one of {CV_CLUSTERS}")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "File must be a PDF")

    CV_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
    target = CV_STORAGE_DIR / f"{cluster}__{safe_name}"

    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    size = target.stat().st_size
    with connect() as con:
        # One CV per cluster — replace on re-upload.
        prev = con.execute(
            "SELECT stored_path FROM cvs WHERE cluster = ?", (cluster,)
        ).fetchone()
        if prev:
            try:
                Path(prev["stored_path"]).unlink(missing_ok=True)
            except Exception:
                pass
            con.execute("DELETE FROM cvs WHERE cluster = ?", (cluster,))
        con.execute(
            "INSERT INTO cvs (cluster, filename, stored_path, size_bytes, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cluster, file.filename, str(target), size, _now_iso()),
        )
        _log(con, "cv_upload", meta={"cluster": cluster, "file": file.filename, "bytes": size})
        con.commit()

    return {"ok": True, "cluster": cluster, "filename": file.filename, "size_bytes": size}


@router.post("/cvs/{cv_id}/delete")
def delete_cv(cv_id: int):
    with connect() as con:
        row = con.execute(
            "SELECT stored_path FROM cvs WHERE id = ?", (cv_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "CV not found")
        try:
            Path(row["stored_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        con.execute("DELETE FROM cvs WHERE id = ?", (cv_id,))
        _log(con, "cv_delete", meta={"id": cv_id})
        con.commit()
    return {"ok": True}
