#!/usr/bin/env python3
"""KamuRadar toplayici: birden cok kaynaktan ilan toplar, ilanlar.json'a yazar.

Her kaynak ayri calisir; biri hata verirse digerleri devam eder. Sonuc durumu
(tamam / hata / kontrol-edilmeli) ilanlar.json icindeki "kaynaklar" alanina yazilir,
sitede kaynak rozetleri olarak gorunur.

Kaynak listesi:
  1) ilan.gov.tr         (Basin Ilan Kurumu, ic servisi)
  2) Kariyer Kapisi      (resmi RSS)
  3) ek_kaynaklar.json   (RSS ya da HTML sayfa: istedigin kadar kaynak eklenebilir)
  4) manuel_ilanlar.json (elle yazdigin ilanlar)

Bilgisayarinda:  python ilan_cek_web.py
"""
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

try:
    import truststore  # Windows sertifika deposu (SSL hatasini onler)
    truststore.inject_into_ssl()
except ImportError:
    pass

import requests
import traceback

for _akis in (sys.stdout, sys.stderr):
    try:
        _akis.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import ayristir as ic

KLASOR = os.path.dirname(os.path.abspath(__file__))
CIKTI = os.path.join(KLASOR, "ilanlar.json")
BELGE_KLASORU = os.path.join(KLASOR, "belgeler")   # indirilen ilan PDF'leri (sayfadan dogrudan acilir)
EK_KAYNAK_DOSYASI = os.path.join(KLASOR, "ek_kaynaklar.json")
MANUEL_DOSYASI = os.path.join(KLASOR, "manuel_ilanlar.json")
ESKI_GUN = 30            # son basvurusu bu kadar gun once gecenleri sil
TARIHSIZ_GUN = 60        # son tarihi olmayanlari, ilk gorulmesinden bu kadar gun sonra sil
ACIKLAMA_SINIR = 2500
SURUM = 3               # veri bicimi/surumu; eski surumde sayfa otomatik yeniden ceker
IL_YENI_LIMIT = 250     # ilan.gov.tr: bir calismada en fazla bu kadar YENI ilan alinir (kalani sonraki calismada)
DETAY_LIMIT = 80         # her kaynakta bir calismada en fazla bu kadar ilan sayfasi acilir
UA = {"user-agent": "Mozilla/5.0 (compatible; KamuRadar/1.0; kisisel kullanim)"}


def iso(d):
    return d.isoformat() if d else None


def satir_id(link):
    return int(hashlib.md5(link.encode("utf-8")).hexdigest()[:8], 16)


def temel_satir(link, **alanlar):
    row = {
        "id": satir_id(link), "institution": "", "title": "", "city": "", "department": "Herhangi",
        "education_level": "", "kpss_type": "", "min_kpss": None, "quota": None,
        "deadline": None, "application_start": None, "link": link, "source_url": link,
        "source_name": "", "employment_type": "Diğer", "description": "",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    row.update(alanlar)
    return row


def detay_oku(session, url):
    """Ilan sayfasini acip duz metnini dondurur (hata olursa bos)."""
    try:
        r = session.get(url, headers=UA, timeout=25)
        if r.status_code != 200:
            return ""
        return ic.duz_metin(r.text)[:ACIKLAMA_SINIR]
    except requests.RequestException:
        return ""


ARA_KAYIT = [None]          # main() doldurur: her 10 yeni ilanda dosyaya yazar (sayfa bekletmeden gostersin)
BELGE_ARDISIK_HATA = [0]    # ust uste indirilemeyen belge sayisi (site engelliyorsa bosuna beklemeyelim)
BELGE_TANI = []   # belge_tani.txt icin: ilk birkac ilanin ne dondurdugu


def _tani_ekle(satir):
    if len(BELGE_TANI) < 14:
        BELGE_TANI.append(satir)


def belge_tani_yaz():
    try:
        with open(os.path.join(KLASOR, "belge_tani.txt"), "w", encoding="utf-8") as f:
            f.write("Kamu Ilan belge indirme tanisi (ilk ilanlar)\n" + "\n".join(BELGE_TANI) + "\n")
    except OSError:
        pass


_PDF_ADAY_RE = re.compile(r"""["']([^"'<>\s]*pdf[^"'<>\s]*)["']""", re.I)


def _uzanti(veri):
    if veri[:5] == b"%PDF-":
        return ".pdf"
    if veri[:4] == b"\x89PNG":
        return ".png"
    if veri[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if veri[:4] == b"\xd0\xcf\x11\xe0":
        return ".doc"
    if veri[:4] == b"PK\x03\x04":
        return ".docx"
    return None


def ara_kaydet(sayi):
    if ARA_KAYIT[0] and sayi % 10 == 0:
        ARA_KAYIT[0]()


def belge_indir(session, url, ad_anahtari, kaydet=True):
    if BELGE_ARDISIK_HATA[0] >= 6:
        if BELGE_ARDISIK_HATA[0] == 6:
            _tani_ekle("!! ust uste 6 belge indirilemedi; bu calismada indirme durduruldu")
            BELGE_ARDISIK_HATA[0] += 1
        return "", ""
    metin, yerel = _belge_indir_ic(session, url, ad_anahtari, kaydet)
    BELGE_ARDISIK_HATA[0] = 0 if (yerel or metin) else BELGE_ARDISIK_HATA[0] + 1
    return metin, yerel


def _belge_indir_ic(session, url, ad_anahtari, kaydet=True):
    """Ilan adresini acar. PDF/gorsel ise belgeler/ klasorune kaydeder; sayfa HTML ise
    icinde gomulu PDF adresi arar. (metin, yerel_yol) dondurur; yerel_yol 'belgeler/xxx.pdf' ya da ''."""
    kisa = url if len(url) < 110 else url[:70] + "..." + url[-25:]
    try:
        r = session.get(url, headers=UA, timeout=30)
    except requests.RequestException as e:
        _tani_ekle(f"{kisa}\n   -> HATA: {type(e).__name__}")
        return "", ""
    tur = r.headers.get("content-type", "?")
    _tani_ekle(f"{kisa}\n   -> durum {r.status_code}, tur {tur}, {len(r.content)} bayt, ilk baytlar {r.content[:40]!r}")
    if r.status_code != 200 or not r.content:
        return "", ""
    veri = r.content
    uzanti = _uzanti(veri)
    if uzanti is None:   # HTML: icinde gomulu PDF var mi?
        adaylar = []
        for m in _PDF_ADAY_RE.finditer(r.text):
            c = m.group(1)
            if c.lower().startswith(("application/", "data:")) or not re.search(r"[/.?=]", c):
                continue
            u = urljoin(r.url, c)
            if u != url and u not in adaylar:
                adaylar.append(u)
        for u in adaylar[:3]:
            try:
                r2 = session.get(u, headers=UA, timeout=30)
            except requests.RequestException:
                continue
            _tani_ekle(f"   gomulu aday {u[:110]} -> {r2.status_code}, {r2.headers.get('content-type', '?')}")
            if r2.status_code == 200 and r2.content[:5] == b"%PDF-":
                veri, uzanti = r2.content, ".pdf"
                break
        if uzanti is None:
            _tani_ekle("   -> PDF bulunamadi; sayfa metni: " + ic.duz_metin(r.text)[:160])
            return ic.duz_metin(r.text)[:60000], ""
    if not kaydet:   # adresi kalici olan kaynaklar: sadece metni oku, dosyayi saklama
        metin = ic.pdf_metni(veri)[:60000] if uzanti == ".pdf" else ""
        _tani_ekle(f"   -> okundu (kaydedilmedi), metin {len(metin)} karakter")
        return metin, ""
    os.makedirs(BELGE_KLASORU, exist_ok=True)
    ad = hashlib.md5(ad_anahtari.encode("utf-8")).hexdigest()[:16] + uzanti
    with open(os.path.join(BELGE_KLASORU, ad), "wb") as f:
        f.write(veri)
    metin = ic.pdf_metni(veri)[:60000] if uzanti == ".pdf" else ""
    _tani_ekle(f"   -> KAYDEDILDI belgeler/{ad}, metin {len(metin)} karakter")
    return metin, "belgeler/" + ad


def belge_sil(row):
    yol = row.get("acik_link") or ""
    if yol.startswith("belgeler/"):
        try:
            os.remove(os.path.join(KLASOR, yol.replace("/", os.sep)))
        except OSError:
            pass


def detaydan_doldur(row, metin):
    """Detay metninden bos kalan alanlari doldurur."""
    if not metin:
        return
    tum = f"{row.get('title', '')} {metin}"
    row["description"] = (row.get("description", "") + " | " + metin)[:ACIKLAMA_SINIR]
    if not row.get("min_kpss"):
        row["min_kpss"] = ic.parse_min_kpss(tum)
    if row.get("kpss_type") in ("", None):
        row["kpss_type"] = ic.parse_kpss_type(tum)
    if not row.get("education_level"):
        row["education_level"] = ic.parse_education(tum)
    if not row.get("deadline"):
        b, e = ic.parse_application_dates(metin)
        row["deadline"] = row["deadline"] or iso(e)
        row["application_start"] = row.get("application_start") or iso(b)
    if not row.get("city"):
        row["city"] = ic.bul_sehir(f"{row.get('institution', '')} {row.get('title', '')}", metin)
    if row.get("employment_type") in ("", None, "Diğer"):
        row["employment_type"] = ic.guess_employment(tum)


# ---------------------------------------------------------------- 1) ilan.gov.tr
def ilan_gov_tr(session, mevcut, **_):
    eklenen = 0
    for page in range(ic.MAX_PAGES):
        keys = {"ats": [ic.AD_TYPE_PERSONEL]}
        if page > 0:
            keys["currentPage"] = [page + 1]
        payload = {"keys": keys, "skipCount": page * ic.PAGE_SIZE, "maxResultCount": ic.PAGE_SIZE}
        r = session.post(ic.SEARCH_URL, json=payload, headers=ic.HEADERS, timeout=30)
        r.raise_for_status()
        result = r.json().get("result") or {}
        ads = result.get("ads") or []
        if page == 0:
            print(f"[ilan.gov.tr] sitedeki toplam ilan: {result.get('numFound', '?')}")
        if not ads:
            break
        toplam = int(result.get("numFound") or 0)
        for ad in ads:
            if ad.get("isArchived") or not ad.get("urlStr"):
                continue
            akademik = any(w in ic.norm(f"{ad.get('title', '')} {ad.get('advertiserName', '')}") for w in ic.SKIP_WORDS)
            link = ic.build_link(ad["urlStr"])
            if link in mevcut:
                continue
            if eklenen >= IL_YENI_LIMIT:
                print(f"[ilan.gov.tr] bu calismada {IL_YENI_LIMIT} yeni ilan alindi; kalanlar sonraki calismada.")
                return eklenen
            time.sleep(ic.DELAY)
            try:
                body, _d = ic.fetch_detail(session, ad.get("id"))
            except (requests.RequestException, ValueError) as e:
                print(f"Detay alinamadi ({ad.get('id')}): {e}")
                body = ""
            metin = f"{ad.get('title', '')} {body}"
            bas, bit = ic.parse_application_dates(metin)
            mevcut[link] = temel_satir(
                link, institution=ad.get("advertiserName") or "", title=ad.get("title") or "",
                city=ad.get("addressCityName") or "", education_level=ic.parse_education(metin),
                kpss_type=ic.parse_kpss_type(metin), min_kpss=ic.parse_min_kpss(metin),
                deadline=iso(bit), application_start=iso(bas),
                source_name="ilan.gov.tr", employment_type=ic.guess_employment(metin),
                description=body[:ACIKLAMA_SINIR], akademik=akademik)
            eklenen += 1
            ara_kaydet(eklenen)
            print(f"+ {ad.get('advertiserName')} - {ad.get('title')}")
        time.sleep(ic.DELAY)
        if toplam and (page + 1) * ic.PAGE_SIZE >= toplam:
            break
    return eklenen


# ---------------------------------------------------------------- 2) Kariyer Kapisi
def kariyer_kapisi(session, mevcut, **_):
    r = session.get(ic.KK_RSS_URL, headers={**UA, "accept": "application/rss+xml, application/xml"}, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    eklenen = 0
    detay_hak = DETAY_LIMIT
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        baslik = (item.findtext("title") or "").strip()
        kategori = (item.findtext("category") or "").strip()
        if not link or not baslik or any(w in ic.norm(kategori) for w in ic.KK_ATLA_KATEGORI):
            continue
        try:
            tarih = parsedate_to_datetime(item.findtext("pubDate") or "").date()
        except (TypeError, ValueError):
            tarih = None
        if link in mevcut:
            if tarih and not mevcut[link].get("application_start"):
                mevcut[link]["application_start"] = iso(tarih)
            continue
        kurum, ilan_basligi = ic.kk_ayir(baslik)
        metin = f"{baslik} {kategori}"
        mevcut[link] = temel_satir(
            link, institution=kurum or ilan_basligi, title=ilan_basligi, application_start=iso(tarih),
            source_name="Kariyer Kapısı", employment_type=ic.kk_calisma_turu(kategori),
            description=f"{baslik} ({kategori})", education_level=ic.parse_education(metin),
            akademik=any(w in ic.norm(metin) for w in ic.SKIP_WORDS))
        if detay_hak > 0:
            detay_hak -= 1
            time.sleep(0.4)
            detaydan_doldur(mevcut[link], detay_oku(session, link))
        mevcut[link]["city"] = mevcut[link].get("city") or ic.bul_sehir(f"{kurum} {ilan_basligi}")
        eklenen += 1
        ara_kaydet(eklenen)
        print(f"+ {kurum or ilan_basligi} - {ilan_basligi}")
    return eklenen


# ---------------------------------------------------------------- 3) ek kaynaklar
class _LinkOkuyucu(HTMLParser):
    def __init__(self):
        super().__init__()
        self.linkler, self._href, self._metin = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._metin = []

    def handle_data(self, data):
        if self._href is not None:
            self._metin.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.linkler.append((self._href, re.sub(r"\s+", " ", "".join(self._metin)).strip()))
            self._href = None


def ek_kaynak(session, mevcut, kaynak=None, **_):
    """ek_kaynaklar.json'daki bir kaynagi okur. tur: 'rss' ya da 'html'."""
    ad, url = kaynak["ad"], kaynak["url"]
    anahtar = [ic.norm(k) for k in kaynak.get("anahtar", [])]
    kontrol = bool(kaynak.get("deneme", False))
    r = session.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    bulunan = []   # (link, baslik, tarih)
    if kaynak.get("tur") == "rss":
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            try:
                t = parsedate_to_datetime(item.findtext("pubDate") or "").date()
            except (TypeError, ValueError):
                t = None
            bulunan.append(((item.findtext("link") or "").strip(), (item.findtext("title") or "").strip(), t))
    else:
        p = _LinkOkuyucu()
        p.feed(r.text)
        for href, metin in p.linkler:
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                bulunan.append((urljoin(url, href), metin, None))
    if kaynak.get("belge"):
        for h_, t_, _ in bulunan[:4]:
            _tani_ekle(f"ornek baglanti: {h_[:110]} | {t_[:60]}")
    link_liste = bool(kaynak.get("link_liste", False))   # detay adresi gecici ise listeye yonlendir
    if link_liste:   # eski (gecersiz detay adresli) kayitlari temizle
        for l in [l for l, r_ in mevcut.items() if r_.get("source_name") == ad and "#" not in l]:
            del mevcut[l]
    eklenen = 0
    detay_hak = DETAY_LIMIT if kaynak.get("detay") else 0
    belge_kaydet = kaynak.get("belge_kaydet", True)
    for gercek, baslik, tarih in bulunan:
        if gercek and gercek.lower().split("?")[0].endswith(".pdf") and len(baslik) < 25:
            baslik = ic.slug_baslik(gercek)   # "ilani gor" gibi kisa baglanti metni yerine dosya adindan baslik
        if not gercek or len(baslik) < 25:
            continue
        if anahtar and not any(k in ic.norm(baslik) for k in anahtar):
            continue
        baslik = ic.baslik_duzelt(baslik)
        link = (url + "#" + hashlib.md5(baslik.encode("utf-8")).hexdigest()[:10]) if link_liste else gercek
        if link in mevcut:
            # belgesi daha once indirilememis ilani (en fazla 3 kez) yeniden dene
            eski = mevcut[link]
            if kaynak.get("belge") and belge_kaydet and not eski.get("acik_link") and eski.get("belge_deneme", 0) < 3 and detay_hak > 0:
                detay_hak -= 1
                eski["belge_deneme"] = eski.get("belge_deneme", 0) + 1
                time.sleep(0.4)
                metin, yerel = belge_indir(session, gercek, baslik)
                eski["orijinal_link"] = gercek
                if yerel:
                    eski["acik_link"] = yerel
                    eski["kontrol_gerekli"] = False
                    detaydan_doldur(eski, metin)
            continue
        bas, bit = ic.parse_tarih_araligi(baslik)
        if not bit:
            bas, bit = ic.parse_application_dates(baslik)
        kurum, kont = ic.baslik_ayir(baslik)
        mevcut[link] = temel_satir(
            link, institution=kurum or kaynak.get("kurum") or ad, title=baslik, source_name=ad, source_url=url,
            application_start=iso(bas or tarih), deadline=iso(bit), quota=kont,
            education_level=ic.parse_education(baslik), kpss_type=ic.parse_kpss_type(baslik),
            min_kpss=ic.parse_min_kpss(baslik), employment_type=ic.guess_employment(baslik),
            description=baslik, kontrol_gerekli=kontrol,
            akademik=any(w in ic.norm(baslik) for w in ic.SKIP_WORDS))
        if detay_hak > 0:
            detay_hak -= 1
            time.sleep(0.4)
            if kaynak.get("belge"):
                metin, yerel = belge_indir(session, gercek, baslik, belge_kaydet)
                if yerel:
                    mevcut[link]["acik_link"] = yerel          # ilanin kendisi (kaydedilmis kopya)
                    mevcut[link]["kontrol_gerekli"] = False
                mevcut[link]["orijinal_link"] = gercek
                if belge_kaydet and yerel.endswith(".pdf") and not metin and not ic.pdf_metni(b""):
                    mevcut[link]["not"] = "PDF metni okunamadi (pypdf kurulu degil olabilir)"
                detaydan_doldur(mevcut[link], metin)
            else:
                detaydan_doldur(mevcut[link], detay_oku(session, gercek))
        mevcut[link]["city"] = mevcut[link].get("city") or ic.bul_sehir(mevcut[link]["institution"] + " " + baslik)
        eklenen += 1
        ara_kaydet(eklenen)
        print(f"+ [{ad}] {baslik}")
    if kaynak.get("belge"):
        belge_tani_yaz()
    return eklenen


# ---------------------------------------------------------------- 4) elle girilenler
def manuel_ilanlar(mevcut):
    if not os.path.exists(MANUEL_DOSYASI):
        return 0
    with open(MANUEL_DOSYASI, encoding="utf-8") as f:
        liste = json.load(f)
    n = 0
    for m in liste:
        if not m.get("link") or not m.get("title"):
            continue
        alanlar = {k: v for k, v in m.items() if k != "link"}
        alanlar.setdefault("source_name", "Manuel ekleme")
        satir = temel_satir(m["link"], **alanlar)
        eski = mevcut.get(m["link"])
        if eski:
            satir["checked_at"] = eski.get("checked_at", satir["checked_at"])
        mevcut[m["link"]] = satir
        n += 1
    return n


# ---------------------------------------------------------------- ortak
def puanlari_yenile(mevcut):
    n = 0
    for row in mevcut.values():
        metin = f"{row.get('title', '')} {row.get('description', '')}"
        if not row.get("min_kpss"):
            p = ic.parse_min_kpss(metin)
            if p is not None:
                row["min_kpss"] = p
                n += 1
        if not row.get("deadline"):
            _, bit = ic.parse_application_dates(metin)
            if bit:
                row["deadline"] = iso(bit)
        if not row.get("education_level"):
            row["education_level"] = ic.parse_education(metin)
        if not row.get("city"):
            row["city"] = ic.bul_sehir(f"{row.get('institution', '')} {row.get('title', '')}", row.get("description", ""))
    return n


def eskileri_sil(mevcut):
    tekrarlari_sil(mevcut)
    bugun = date.today()
    for link, r in list(mevcut.items()):
        if r.get("deadline"):
            if r["deadline"] < (bugun - timedelta(days=ESKI_GUN)).isoformat():
                belge_sil(r)
                del mevcut[link]
        else:
            ilk = (r.get("application_start") or r.get("checked_at") or "")[:10]
            if ilk and ilk < (bugun - timedelta(days=TARIHSIZ_GUN)).isoformat():
                belge_sil(r)
                del mevcut[link]


def tekrarlari_sil(mevcut):
    """Ayni kaynaktaki, noktalama/bosluk disinda ayni baslikli ilanlari tek kayda indirir."""
    goruldu = {}
    for link, r in list(mevcut.items()):
        if r.get("source_name") in ("ilan.gov.tr", "Kariyer Kapısı", "Manuel ekleme"):
            continue   # bunlarin her ilani kendi adresine sahip; birlestirilmez
        anahtar = (r.get("source_name"), re.sub(r"[^A-Z0-9]", "", ic.norm(r.get("title", ""))))
        if not anahtar[1]:
            continue
        if anahtar in goruldu:
            belge_sil(r)
            del mevcut[link]
        else:
            goruldu[anahtar] = link


def imza(ilanlar, kaynaklar):
    return json.dumps([sorted(ilanlar, key=lambda r: r["id"]), kaynaklar], ensure_ascii=False, sort_keys=True)


def yaz_cikti(mevcut, durumlar, onceki):
    """ilanlar.json'u (degistiyse) guvenli sekilde yazar; ilan sayisini dondurur."""
    sayilar = {}
    for r in mevcut.values():
        sayilar[r.get("source_name", "?")] = sayilar.get(r.get("source_name", "?"), 0) + 1
    kaynaklar = {ad: dict(d, sayi=sayilar.get(ad, 0)) for ad, d in durumlar.items()}
    ilanlar = sorted(mevcut.values(), key=lambda r: r["id"])
    if imza(ilanlar, kaynaklar) != onceki[0]:
        gecici = CIKTI + ".tmp"
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump({"surum": SURUM, "guncelleme": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "kaynaklar": kaynaklar, "ilanlar": ilanlar}, f, ensure_ascii=False, indent=1)
        os.replace(gecici, CIKTI)
        onceki[0] = imza(ilanlar, kaynaklar)
        print(f"ilanlar.json yazildi: {len(ilanlar)} ilan")
    return len(ilanlar)


def main():
    mevcut, eski_kaynaklar = {}, {}
    if os.path.exists(CIKTI):
        with open(CIKTI, encoding="utf-8") as f:
            eski = json.load(f)
        eski_kaynaklar = eski.get("kaynaklar") or {}
        for row in eski.get("ilanlar") or []:
            mevcut[row["link"]] = row

    # Kapali (aktif: false) kaynaklarin daha once toplanmis ilanlarini listeden kaldir
    kapali = set()
    if os.path.exists(EK_KAYNAK_DOSYASI):
        with open(EK_KAYNAK_DOSYASI, encoding="utf-8") as f:
            kapali = {k["ad"] for k in json.load(f) if not k.get("aktif", True)}
    for link, r in list(mevcut.items()):
        if r.get("source_name") in kapali:
            belge_sil(r)
            del mevcut[link]
    for ad in kapali:
        eski_kaynaklar.pop(ad, None)

    onceki = [imza(list(mevcut.values()), eski_kaynaklar)]

    isler = [("ilan.gov.tr", ilan_gov_tr, {}), ("Kariyer Kapısı", kariyer_kapisi, {})]
    if os.path.exists(EK_KAYNAK_DOSYASI):
        with open(EK_KAYNAK_DOSYASI, encoding="utf-8") as f:
            for k in json.load(f):
                if k.get("aktif", True):
                    isler.append((k["ad"], ek_kaynak, {"kaynak": k}))

    session = requests.Session()
    durumlar = {}
    BELGE_ARDISIK_HATA[0] = 0
    ARA_KAYIT[0] = lambda: yaz_cikti(mevcut, {**eski_kaynaklar, **durumlar}, onceki)
    for ad, fn, ekstra in isler:
        print(f"--- {ad}")
        try:
            eklenen = fn(session, mevcut, **ekstra)
            durumlar[ad] = {"durum": "tamam", "yeni": eklenen}
            if ekstra.get("kaynak", {}).get("deneme"):
                durumlar[ad]["not"] = "deneme kaynağı: ilanlar resmî sayfadan kontrol edilmeli"
            print(f"{ad}: tamam, {eklenen} yeni")
        except Exception as e:  # bir kaynak cokse digerleri calissin
            mesaj = f"{type(e).__name__}: {e}"
            print(f"! {ad} hata verdi: {mesaj}")
          traceback.print_exc()
            durumlar[ad] = {"durum": "hata", "mesaj": mesaj}
        # her kaynaktan sonra kaydet: sayfa, bitmesini beklemeden ilanlari gostersin
        eskileri_sil(mevcut)
        yaz_cikti(mevcut, {**eski_kaynaklar, **durumlar}, onceki)

    print(f"Manuel ilan: {manuel_ilanlar(mevcut)}")
    print(f"Puani/tarihi yeni bulunan: {puanlari_yenile(mevcut)}")
    eskileri_sil(mevcut)
    toplam = yaz_cikti(mevcut, durumlar, onceki)
    print(f"Bitti. Eklenen: {sum(d.get('yeni', 0) for d in durumlar.values())} (toplam {toplam} ilan)")
    if all(d["durum"] == "hata" for d in durumlar.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
