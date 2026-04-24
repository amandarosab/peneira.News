import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")
_IS_VERCEL = os.environ.get("VERCEL", "") == "1"
_LOCAL_STORE_FILE = Path("/tmp/private_submissions.json") if _IS_VERCEL else _BASE_DIR / "private_submissions.json"
_REMOTE_STORE_ID = os.environ.get("PRIVATE_STORAGE_ID", "").strip()
_REMOTE_CREDENTIALS_FILE = os.environ.get("PRIVATE_STORAGE_CREDENTIALS_FILE", "").strip()
_REMOTE_CREDENTIALS_JSON = os.environ.get("PRIVATE_STORAGE_CREDENTIALS_JSON", "").strip()
_ALLOW_VERCEL_LOCAL_FALLBACK = os.environ.get("PRIVATE_STORAGE_ALLOW_VERCEL_LOCAL_FALLBACK", "").strip() == "1"
_TAB_SUGGESTIONS = os.environ.get("PRIVATE_STORAGE_TAB_SUGGESTIONS", "Sugestoes")
_TAB_CONTACT = os.environ.get("PRIVATE_STORAGE_TAB_CONTACT", "Contato")
_PRIVATE_STORE_ALERT_WEBHOOK = os.environ.get("PRIVATE_STORE_ALERT_WEBHOOK", "").strip()
_BRT = timezone(timedelta(hours=-3))

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    gspread = None
    Credentials = None


class PrivateStoreError(RuntimeError):
    pass


def _resolved_credentials_file():
    if not _REMOTE_CREDENTIALS_FILE:
        return None
    candidate = Path(_REMOTE_CREDENTIALS_FILE)
    if not candidate.is_absolute():
        candidate = _BASE_DIR / candidate
    return candidate


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
    credentials_file = _resolved_credentials_file()
    has_credentials = bool(_REMOTE_CREDENTIALS_JSON or (credentials_file and credentials_file.exists()))
    return bool(_REMOTE_STORE_ID and has_credentials and gspread and Credentials)


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
    credentials_file = _resolved_credentials_file()
    try:
        if _REMOTE_CREDENTIALS_JSON:
            info = json.loads(_REMOTE_CREDENTIALS_JSON)
            creds = creds_factory.from_service_account_info(info, scopes=scopes)
        else:
            if credentials_file is None or not credentials_file.exists():
                raise PrivateStoreError("Arquivo de credenciais remoto não encontrado.")
            creds = creds_factory.from_service_account_file(str(credentials_file), scopes=scopes)
        return gspread_module.authorize(creds)
    except Exception as exc:
        if isinstance(exc, PrivateStoreError):
            raise
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
    credentials_file = _resolved_credentials_file()
    remote_configured = bool(
        _REMOTE_STORE_ID and (_REMOTE_CREDENTIALS_JSON or _REMOTE_CREDENTIALS_FILE)
    )
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
        "credentials_file_exists": bool(credentials_file and credentials_file.exists()),
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
    # Se remoto está habilitado, tenta gravar remotamente. Se falhar,
    # tenta fallback local a menos que a implantação exija remoto estrito.
    strict_remote = os.environ.get("PRIVATE_STORAGE_STRICT_REMOTE", "0").strip() == "1"

    if _remote_enabled():
        try:
            _append_remote(kind, payload)
            return "remote"
        except Exception as exc:
            logger.error(f"Falha ao gravar no armazenamento remoto: {exc}")
            # optional alert webhook for ops/monitoring
            if _PRIVATE_STORE_ALERT_WEBHOOK:
                try:
                    import requests

                    requests.post(_PRIVATE_STORE_ALERT_WEBHOOK, json={
                        "event": "private_store.remote_failure",
                        "error": str(exc),
                        "kind": kind,
                        "time": datetime.now(_BRT).isoformat(),
                    }, timeout=3)
                except Exception:
                    logger.exception("Falha ao notificar webhook de alerta do private_store")
            if strict_remote:
                # Repassa erro para o chamador — ambiente exige remoto.
                raise PrivateStoreError("Falha ao gravar no armazenamento remoto.") from exc
            # Caso contrário, cai para fallback local
            logger.warning("Falha no remoto — usando fallback local.")

    # Se estamos em Vercel e fallback local NÃO é permitido, bloqueia.
    if _IS_VERCEL and not _ALLOW_VERCEL_LOCAL_FALLBACK:
        raise PrivateStoreError(
            "Armazenamento remoto não configurado na Vercel. "
            "Defina PRIVATE_STORAGE_ID e PRIVATE_STORAGE_CREDENTIALS_JSON."
        )

    logger.info("Usando armazenamento local para submissão privada.")
    _append_local(kind, payload)
    return "local"
