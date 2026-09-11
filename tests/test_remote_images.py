from __future__ import annotations

from redmail.remote_images import MAX_IMAGES, embed_remote_images


def test_remote_images_become_inline_cid_and_are_downloaded_once() -> None:
    # Жалоба (повторная): "опять есть проблема с отображением картинок при
    # пересылке" — внешние <img src="https://…"> рассылок при пересылке
    # должны уходить внутри письма как cid:, а не оставаться ссылками,
    # которые редактор не показывает, а получатель не грузит.
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        return ("image/png", b"PNGDATA-" + url.encode())

    html = (
        '<p><img src="https://cdn.example.com/logo.png" alt="logo"> '
        "<IMG SRC='https://cdn.example.com/logo.png'> "
        '<img width="1" src="https://t.example.com/pixel.gif"></p>'
    )
    out, images = embed_remote_images(html, {"old@x": ("image/jpeg", b"J")}, fetch=fetch)

    assert calls == ["https://cdn.example.com/logo.png", "https://t.example.com/pixel.gif"]  # одинаковый URL — один раз
    assert "https://" not in out
    assert out.count("cid:fwd-") == 3
    assert images["old@x"] == ("image/jpeg", b"J")  # прежние встроенные картинки сохранены
    assert len(images) == 3
    cid = out.split('src="cid:')[1].split('"')[0]
    assert images[cid][1].startswith(b"PNGDATA-")


def test_failed_downloads_keep_original_links_and_cid_images_untouched() -> None:
    html = '<img src="https://down.example.com/a.png"><img src="cid:inline1"><img src="data:image/png;base64,AAAA">'
    out, images = embed_remote_images(html, {"inline1": ("image/png", b"X")}, fetch=lambda url: None)
    assert out == html
    assert images == {"inline1": ("image/png", b"X")}


def test_download_count_is_capped() -> None:
    html = "".join(f'<img src="https://e.com/{i}.png">' for i in range(MAX_IMAGES + 5))
    fetched: list[str] = []

    def fetch(url: str):
        fetched.append(url)
        return ("image/png", b"x")

    out, images = embed_remote_images(html, None, fetch=fetch)
    assert len(fetched) == MAX_IMAGES
    assert out.count("cid:") == MAX_IMAGES
    assert out.count("https://") == 5
