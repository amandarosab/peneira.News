import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
_IS_VERCEL = os.environ.get("VERCEL", "") == "1"
_LOCAL_STORE_FILE = Path("/tmp/private_submissions.json") if _IS_VERCEL else _BASE_DIR / "private_submissions.json"
_REMOTE_STORE_ID = os.environ.get("PRIVATE_STORAGE_ID", "").strip()
_REMOTE_CREDENTIALS_FILE = os.environ.get("PRIVATE_STORAGE_CREDENTIALS_FILE", "").strip()
_REMOTE_CREDENTIALS_JSON = os.environ.get("PRIVATE_STORAGE_CREDENTIALS_JSON", "").strip()
_ALLOW_VERCEL_LOCAL_FALLBACK = os.environ.get("PRIVATE_STORAGE_ALLOW_VERCEL_LOCAL_FALLBACK", "").strip() == "1"
_TAB_SUGGESTIONS = os.environ.get("PRIVATE_STORAGE_TAB_SUGGESTIONS", "Sugestoes")
_TAB_CONTACT = os.environ.get("PRIVATE_STORAGE_TAB_CONTACT", "Contato")
_BRT = timezone(timedelta(hours=-3))

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    gspread = None
    Credentials = None


class PrivateStoreError(RuntimeError):
    pass


_HEADERS = {
    "suggestion": [
        "enviado_em",
        "enviado_em_iso",
        "nome",
        "email",
        "link_sugerido",
        "mensagem",
        "origem",
    ],
    "contact": [
        "enviado_em",
        "enviado_em_iso",
        "nome",
        "sobrenome",
        "email",
        "telefone",
        "mensagem",
        "origem",
    ],
}


def _now_fields():
    now = datetime.now(_BRT)
    return {
        "enviado_em": now.strftime("%d/%m/%Y %H:%M:%S"),
        "enviado_em_iso": now.isoformat(),
    }


def _build_record(kind, payload):
    if kind not in _HEADERS:
        raise PrivateStoreError("Tipo de envio inválido.")
    record = _now_fields()
    for header in _HEADERS[kind]:
        record.setdefault(header, "")
    for key, value in payload.items():
        record[key] = "" if value is None else str(value).strip()
    return record


def _remote_enabled():
    return bool(_REMOTE_STORE_ID and (_REMOTE_CREDENTIALS_FILE or _REMOTE_CREDENTIALS_JSON) and gspread and Credentials)


def _build_remote_client():
    if not _remote_enabled():
        raise PrivateStoreError("Armazenamento remoto não configurado.")

    creds_factory = Credentials
    gspread_module = gspread
    if creds_factory is None or gspread_module is None:
        raise PrivateStoreError("Dependências do armazenamento remoto indisponíveis.")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    try:
        if _REMOTE_CREDENTIALS_JSON:
            info = json.loads(_REMOTE_CREDENTIALS_JSON)
            creds = creds_factory.from_service_account_info(info, scopes=scopes)
        else:
            creds = creds_factory.from_service_account_file(_REMOTE_CREDENTIALS_FILE, scopes=scopes)
        return gspread_module.authorize(creds)
    except Exception as exc:
        raise PrivateStoreError("Falha ao inicializar armazenamento remoto.") from exc


def _worksheet_name(kind):
    return _TAB_SUGGESTIONS if kind == "suggestion" else _TAB_CONTACT


def _get_or_create_worksheet(spreadsheet, kind):
    title = _worksheet_name(kind)
    headers = _HEADERS[kind]
    try:
        worksheet = spreadsheet.worksheet(title)
    except Exception:
        worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=max(12, len(headers) + 2))
        worksheet.append_row(headers)
        return worksheet

    first_row = worksheet.row_values(1)
    if first_row != headers:
        if not first_row:
            worksheet.append_row(headers)
        else:
            worksheet.insert_row(headers, 1)
    return worksheet


def _append_remote(kind, payload):
    client = _build_remote_client()
    try:
        spreadsheet = client.open_by_key(_REMOTE_STORE_ID)
        worksheet = _get_or_create_worksheet(spreadsheet, kind)
        record = _build_record(kind, payload)
        row = [record.get(header, "") for header in _HEADERS[kind]]
        worksheet.append_row(row)
    except PrivateStoreError:
        raise
    except Exception as exc:
        raise PrivateStoreError("Falha ao gravar no armazenamento remoto.") from exc


def _read_local_store():
    if _LOCAL_STORE_FILE.exists():
        try:
            with open(_LOCAL_STORE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"suggestion": [], "contact": []}


def _write_local_store(data):
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(_LOCAL_STORE_FILE.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(_LOCAL_STORE_FILE))
    except OSError as exc:
        raise PrivateStoreError("Falha ao gravar armazenamento local.") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _append_local(kind, payload):
    record = _build_record(kind, payload)
    store = _read_local_store()
    store.setdefault(kind, []).append(record)
    _write_local_store(store)


def get_storage_diagnostics(*, probe_remote=False):
    remote_configured = bool(_REMOTE_STORE_ID and (_REMOTE_CREDENTIALS_FILE or _REMOTE_CREDENTIALS_JSON))
    remote_dependencies_ok = bool(gspread and Credentials)

    if _remote_enabled():
        effective_mode = "remote"
    elif _IS_VERCEL and not _ALLOW_VERCEL_LOCAL_FALLBACK:
        effective_mode = "blocked"
    else:
        effective_mode = "local"

    diagnostics = {
        "is_vercel": _IS_VERCEL,
        "effective_mode": effective_mode,
        "remote_configured": remote_configured,
        "remote_dependencies_ok": remote_dependencies_ok,
        "remote_store_id_configured": bool(_REMOTE_STORE_ID),
        "credentials_source": "json" if _REMOTE_CREDENTIALS_JSON else ("file" if _REMOTE_CREDENTIALS_FILE else "missing"),
        "local_fallback_allowed": _ALLOW_VERCEL_LOCAL_FALLBACK,
        "worksheet_suggestions": _TAB_SUGGESTIONS,
        "worksheet_contact": _TAB_CONTACT,
    }

    if probe_remote:
        try:
            client = _build_remote_client()
            spreadsheet = client.open_by_key(_REMOTE_STORE_ID)
            diagnostics["remote_probe"] = {
                "ok": True,
                "title": spreadsheet.title,
            }
        except Exception as exc:
            diagnostics["remote_probe"] = {
                "ok": False,
                "error": str(exc),
            }

    return diagnostics


def save_submission(kind, payload):
    if kind not in _HEADERS:
        raise PrivateStoreError("Tipo de envio inválido.")

    if _remote_enabled():
        _append_remote(kind, payload)
        return "remote"

    if _IS_VERCEL and not _ALLOW_VERCEL_LOCAL_FALLBACK:
        raise PrivateStoreError(
            "Armazenamento remoto não configurado na Vercel. "
            "Defina PRIVATE_STORAGE_ID e PRIVATE_STORAGE_CREDENTIALS_JSON."
        )

    logger.warning("Armazenamento remoto não configurado; usando fallback privado local.")
    _append_local(kind, payload)
    return "local"
