"""
Google Drive API РєР»РёРµРЅС‚ РґР»СЏ СЂР°Р±РѕС‚С‹ СЃ С„РѕС‚РѕРіСЂР°С„РёСЏРјРё С‚РѕРІР°СЂРѕРІ.
"""

import logging
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import GOOGLE_CREDENTIALS_FILE, GOOGLE_DRIVE_PHOTOS_FOLDER_ID

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_service = None


def get_drive_service():
    """РџРѕР»СѓС‡РёС‚СЊ (РёР»Рё СЃРѕР·РґР°С‚СЊ) РєР»РёРµРЅС‚ Google Drive API."""
    global _service
    if _service is None:
        creds = Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
        _service = build("drive", "v3", credentials=creds)
    return _service


def list_folders_in_folder(parent_folder_id: str) -> list[dict]:
    """РЎРїРёСЃРѕРє РїРѕРґРїР°РїРѕРє РІ РїР°РїРєРµ Google Drive."""
    service = get_drive_service()
    query = (
        f"'{parent_folder_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])


def list_images_in_folder(folder_id: str) -> list[dict]:
    """РЎРїРёСЃРѕРє РёР·РѕР±СЂР°Р¶РµРЅРёР№ РІ РїР°РїРєРµ Google Drive."""
    service = get_drive_service()
    query = (
        f"'{folder_id}' in parents "
        f"and mimeType contains 'image/' "
        f"and trashed=false"
    )
    results = (
        service.files()
        .list(q=query, fields="files(id, name, mimeType)")
        .execute()
    )
    return results.get("files", [])


def get_direct_download_url(file_id: str) -> str:
    """РџСЂСЏРјР°СЏ СЃСЃС‹Р»РєР° РґР»СЏ СЃРєР°С‡РёРІР°РЅРёСЏ С„Р°Р№Р»Р° СЃ Google Drive."""
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def download_file_bytes(file_id: str) -> bytes:
    """Скачать файл из Google Drive через API (с авторизацией сервисного аккаунта)."""
    from io import BytesIO
    from googleapiclient.http import MediaIoBaseDownload

    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def build_product_photo_index(root_folder_id: str | None = None) -> dict:
    """
    Построить индекс: название папки товара → список фото.
    Рекурсивно обходит структуру папок.
    """
    root_id = root_folder_id or GOOGLE_DRIVE_PHOTOS_FOLDER_ID
    if not root_id:
        logger.warning("GOOGLE_DRIVE_PHOTOS_FOLDER_ID не задан")
        return {}

    index = {}

    def add_images_for_folder(folder_id, key, path):
        images = list_images_in_folder(folder_id)
        if images:
            index[key.lower()] = {
                "folder_id": folder_id,
                "path": path,
                "images": [
                    {
                        "file_id": img["id"],
                        "filename": img["name"],
                        "direct_url": get_direct_download_url(img["id"]),
                    }
                    for img in images
                ],
            }

    def traverse(folder_id, path=""):
        # Индексируем фото прямо в текущей папке
        if path:
            add_images_for_folder(folder_id, path, path)
        subfolders = list_folders_in_folder(folder_id)
        for folder in subfolders:
            current_path = f"{path}/{folder['name']}" if path else folder["name"]
            add_images_for_folder(folder["id"], folder["name"], current_path)
            traverse(folder["id"], current_path)

    # Если фото лежат прямо в корне — индексируем как ROOT
    add_images_for_folder(root_id, "root", "")
    traverse(root_id)
    logger.info(f"Built photo index: {len(index)} product folders")
    return index
