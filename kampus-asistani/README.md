# Kampüs Asistanı: MCP ile Ajanları Dış Dünyaya Bağlamak

Bir dil modeli (LLM) tek başına ne bugünün yemek menüsünü bilir ne de ödev listene bir şey
ekleyebilir. Bu projede, bir yapay zekâ ajanına **MCP (Model Context Protocol)** üzerinden
"eller" vereceğiz: önce dosyalara, sonra bir PostgreSQL veritabanına erişen araçlar.

**Bu rehberi bitirdiğinde şunları yapabileceksin:**
- Bir MCP server'ın iç yapısını ve agent ile nasıl konuştuğunu açıklamak
- Sıfırdan ve resmi SDK ile MCP server yazmak
- LangGraph ile araç kullanan bir ajan kurmak ve onu MCP server'lara bağlamak
- Docker'da çalışan bir veritabanını ajana güvenli şekilde açmak

Rehber baştan sona sırayla ilerler; her bölüm bir öncekinin üzerine kurulur.

---

## İçindekiler

0. [Temel kavramlar](#0-temel-kavramlar)
1. [Büyük resim](#1-büyük-resim)
2. [Proje yapısı](#2-proje-yapısı)
3. [Kurulum](#3-kurulum)
4. [Bölüm 1: İlk MCP server'ımız](#4-bölüm-1-ilk-mcp-serverımız)
5. [Bölüm 2: LangGraph ile ajan](#5-bölüm-2-langgraph-ile-ajan)
6. [Bölüm 3: Veritabanına geçiş (PostgreSQL + Docker)](#6-bölüm-3-veritabanına-geçiş-postgresql--docker)
7. [Bonus: Claude Desktop'a bağlamak](#7-bonus-claude-desktopa-bağlamak)
8. [Alıştırmalar](#8-alıştırmalar)
9. [Sorun giderme](#9-sorun-giderme)
10. [Komut özeti](#10-komut-özeti)
11. [Ek: Eğitmen notları](#11-ek-eğitmen-notları)

---

## 0. Temel kavramlar

| Kavram | Kısaca |
|---|---|
| **LLM** | Metin üreten dil modeli (Qwen, Llama, Claude...). Kendi başına sadece metin üretir. |
| **Araç (tool)** | LLM'in çağırabildiği bir fonksiyon. Örn. `yemekhane_menusu(gun="salı")`. |
| **Ajan (agent)** | LLM + araçlar + bir döngü. LLM hangi aracı çağıracağına kendisi karar verir, sonucu görür, gerekirse tekrar çağırır. |
| **MCP** | Araçları ajanlara sunmanın **standart** yolu. Bir kez MCP server yazarsın; Claude Desktop, LangGraph, Cursor gibi MCP destekleyen her istemci onu kullanabilir. Bilgisayarların USB'si gibi düşünebilirsin. |
| **MCP server** | Araçları sunan program (bizim `server.py` ve `server_postgres.py`). |
| **MCP istemcisi** | Server'a bağlanıp araçları kullanan taraf (bizim `ajan.py`). |
| **stdio** | İstemci server'ı alt süreç olarak başlatır ve onunla stdin/stdout üzerinden konuşur. Ağ gerekmez. |
| **JSON-RPC** | Mesaj biçimi. Her mesaj tek satırlık bir JSON'dır: `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` |
| **LangGraph** | Ajanın düşünme döngüsünü bir **graf** (düğümler + kenarlar) olarak kurmamızı sağlayan kütüphane. |

Bir MCP konuşmasında üç temel adım vardır:

1. **`initialize`**: İstemci ve server el sıkışır.
2. **`tools/list`**: İstemci, server'daki araçları ve açıklamalarını öğrenir.
3. **`tools/call`**: İstemci bir aracı parametrelerle çağırır, server sonucu döner.

---

## 1. Büyük resim

```
                         ┌─────────────────── ajan.py (LangGraph) ───────────────────┐
                         │                                                           │
  👤 Kullanıcı ────────> │   [ ajan ] ──(araç lazım mı?)──> [ araclar ]              │
                         │      ^  │                            │                    │
                         │      │  └──(hayır)──> Cevap          │                    │
                         │      └──────── sonuçlar ─────────────┘                    │
                         │      │                               │                    │
                         │     LLM                         MCP istemcisi             │
                         │  (Ollama/Claude)                     │                    │
                         └──────────────────────────────────────┼────────────────────┘
                                                                │ MCP (JSON-RPC, stdio)
                                        ┌───────────────────────┴───────────────────────┐
                                        ▼                                               ▼
                                   server.py                                  server_postgres.py
                               (protokol elle yazıldı)                         (resmi SDK: FastMCP)
                                        │                                               │
                                        ▼                                               ▼
                                   veri/*.json                              PostgreSQL (Docker'da)
```

Dikkat: Ajan **aynı**; sadece hangi server'a bağlandığı değişiyor. İki server da aynı adlı
araçları sunduğu için ajan farkı anlamaz bile. MCP'nin gücü burada.

---

## 2. Proje yapısı

```
kampus-asistani/
├── server.py              # Bölüm 1: Sıfırdan, bağımlılıksız MCP server (JSON dosyaları)
├── veri/                  #   └─ server.py'nin okuduğu/yazdığı dosyalar
│   ├── menu.json
│   ├── ders_programi.json
│   └── odevler.json
├── ajan.py                # Bölüm 2: LangGraph ajanı (MCP istemcisi)
├── server_postgres.py     # Bölüm 3: FastMCP ile PostgreSQL server'ı (+ sql_sorgula aracı)
├── docker-compose.yml     #   └─ PostgreSQL'i Docker'da başlatır
├── postgres/init.sql      #   └─ Tablolar, örnek veriler, salt okunur kullanıcı
├── postgres_test.py       #   └─ PostgreSQL server'ını tek komutla test eder
└── requirements.txt       # Python paketleri
```

**Araçlar** (iki server'da da aynı adlarla):

| Araç | Ne yapar | server.py | server_postgres.py |
|---|---|:-:|:-:|
| `yemekhane_menusu(gun)` | Günün menüsü | ✅ | ✅ (+kalori) |
| `ders_programi(gun)` | Günün dersleri | ✅ | ✅ (+öğretim üyesi) |
| `odevleri_listele(sadece_bekleyenler)` | Ödev listesi | ✅ | ✅ |
| `odev_ekle(ders, aciklama, son_tarih)` | Yeni ödev ekler | ✅ | ✅ |
| `odev_tamamla(id)` | Ödevi bitmiş işaretler | ✅ | ✅ |
| `sql_sorgula(sorgu)` | Salt okunur serbest SQL | — | ✅ |

`gun` parametresi `bugün`, `yarın` veya gün adı (`salı` gibi) olabilir.

---

## 3. Kurulum

### 3.1 Python (3.10 veya üstü)

```bash
python --version        # Windows
python3 --version       # macOS / Linux
```

> Bu rehberde komutlar `python` ile yazıldı. macOS/Linux'ta `python3` kullanman gerekebilir.

### 3.2 Sanal ortam ve paketler

Proje klasöründe:

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

Sonra (her iki sistemde):

```bash
pip install -r requirements.txt
```

> Sanal ortamı her yeni terminalde tekrar etkinleştirmen gerekir (`Activate.ps1` / `source` satırı).
> Etkin olduğunda satır başında `(.venv)` görürsün.

### 3.3 LLM: Ollama (önerilen, ücretsiz, internetsiz çalışır)

1. [ollama.com](https://ollama.com) adresinden Ollama'yı indir ve kur.
2. Araç çağırmayı destekleyen bir model indir:
   ```bash
   ollama pull qwen2.5:7b      # ~4.7 GB, Türkçe'de daha iyi
   ollama pull qwen2.5:3b      # ~1.9 GB, zayıf bilgisayarlar için
   ```
3. Ollama arka planda çalışıyor olmalı (kurulumdan sonra genelde otomatik başlar).
   Kontrol: `ollama list`

**Alternatif: Claude (internet ve API anahtarı gerekir)**
```bash
# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY="sk-ant-..."
```
Sonra ajanı `--model anthropic` ile çalıştırırsın.

### 3.4 Docker (sadece Bölüm 3 için)

[Docker Desktop](https://www.docker.com/products/docker-desktop/)'ı kur ve aç. Kontrol:
```bash
docker --version
docker compose version
```

---

## 4. Bölüm 1: İlk MCP server'ımız

**Dosya:** `server.py` · **Gereken:** Sadece Python (hiçbir paket yok)

Bu server'ı hiçbir kütüphane kullanmadan yazdık ki MCP'nin aslında ne kadar basit olduğu
görülsün: stdin'den bir JSON satırı oku, işle, stdout'a bir JSON satırı yaz.

### 4.1 Kodun anatomisi

`server.py` üç bölümden oluşur:

1. **Araç fonksiyonları:** Sıradan Python fonksiyonları (`yemekhane_menusu`, `odev_ekle`...).
2. **`ARACLAR` sözlüğü:** Her aracın adı, açıklaması (`description`) ve parametre şeması
   (`inputSchema`). Ajan, bu açıklamaları okuyarak hangi aracı ne zaman kullanacağına karar verir.
3. **Protokol döngüsü (`main` ve `mesaji_isle`):** `initialize`, `tools/list` ve `tools/call`
   mesajlarına cevap verir.

### 4.2 Server ile elle konuş

```bash
python server.py
```

Server mesaj bekler ve nasıl deneyeceğini gösterir. Aşağıdaki satırları **tek tek** yapıştırıp
Enter'a bas. Şu an agent'ın yaptığı işi sen yapıyorsun:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"ben","version":"1.0"}}}
```
```json
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```
```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"yemekhane_menusu","arguments":{"gun":"salı"}}}
```
```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"odev_ekle","arguments":{"ders":"Algoritmalar","aciklama":"Deneme","son_tarih":"yarın akşam"}}}
```

Çıkmak için `Ctrl+C`.

Ekranda iki tür satır göreceksin:

```
[19:39:09] ⬅️  GELEN  #3: tools/call → yemekhane_menusu({"gun": "salı"})       ← log (stderr)
[19:39:09] ➡️  GİDEN  #3: ✅ Salı menüsü: Ezogelin çorbası, ... (0.1 ms)        ← log (stderr)
{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", ...}]}}     ← asıl cevap (stdout)
```

**Gözlemle:**
- Her cevabın `id`'si, ilgili isteğin `id`'si ile aynı. İstemci cevapları böyle eşleştirir.
- Son komutta tarih hatalı olduğu için cevapta `"isError": true` var, ama bu bir protokol
  hatası değil, normal bir cevap. Böylece ajan hatayı okuyup düzeltebilir.

### 4.3 İki altın kural

1. **stdout sadece protokol içindir.** Server'da `print()` ile bir şey yazdırırsan istemci onu
   JSON sanıp bozulur. Bu yüzden `log()` fonksiyonu her şeyi **stderr**'e yazar.
   Logları kapatmak için `KAMPUS_LOG=sessiz` ortam değişkenini kullanabilirsin.
2. **İyi açıklama = doğru araç seçimi.** LLM kodunu görmez, sadece `description`'ı okur.

---

## 5. Bölüm 2: LangGraph ile ajan

**Dosya:** `ajan.py` · **Gereken:** `requirements.txt` paketleri + Ollama (veya Claude)

Bölüm 1'de araçları biz çağırdık. Şimdi bu kararı bir LLM'e bırakıyoruz.

### 5.1 Graf

```
   START ──> [ ajan ] ──(son mesajda araç çağrısı var mı?)──> [ araclar ]
                ^                                                  │
                │                                                  │
                └──────────────── araç sonuçları ──────────────────┘
                │
                └──(yok)──> END (cevap kullanıcıya)
```

`ajan.py` içinde bu grafı kuran kod sadece birkaç satırdır (`graf_olustur` fonksiyonu):

```python
graf = StateGraph(MessagesState)                 # Durum: mesaj geçmişi
graf.add_node("ajan", ajan)                      # LLM karar verir
graf.add_node("araclar", ToolNode(araclar))      # Araçları MCP üzerinden çalıştırır
graf.add_edge(START, "ajan")
graf.add_conditional_edges("ajan", tools_condition, {"tools": "araclar", "__end__": "__end__"})
graf.add_edge("araclar", "ajan")                 # Sonuçlarla tekrar LLM'e dön
graf.compile(checkpointer=InMemorySaver())       # Sohbet hafızası
```

**Araçlar nereden geliyor?** Ajan kodunda tek bir araç bile elle tanımlanmadı.
`langchain-mcp-adapters` server'a bağlanır, `tools/list` ile araçları öğrenir ve onları
otomatik olarak LangChain aracına dönüştürür:

```python
async with istemci.session("kampus") as oturum:      # Server'ı başlat, bağlantıyı açık tut
    araclar = await load_mcp_tools(oturum)            # MCP araçları → LangChain araçları
```

### 5.2 Çalıştır

```bash
python ajan.py                                # Ollama ile sohbet
python ajan.py --protokol                     # + server'daki MCP trafiğini de göster
python ajan.py --soru "Yarın derslerim neler?" # Tek soru sor ve çık
python ajan.py --model anthropic              # Claude ile
python ajan.py --model-adi qwen2.5:3b         # Farklı bir Ollama modeli
python ajan.py --graf                         # Grafın Mermaid çizimi (mermaid.live'a yapıştır)
```

Çıktıdaki işaretler:

| İşaret | Anlamı |
|---|---|
| 🔌 | MCP server'a bağlanıldı, araçlar yüklendi |
| 🧠 | LLM bir aracı çağırmaya karar verdi |
| 🔧 | Araç çalıştı, sonucu LLM'e geri gitti |
| 🤖 | LLM'in kullanıcıya son cevabı |
| ⬅️ / ➡️ | (`--protokol` ile) Server'a gelen ve server'dan giden MCP mesajları |

### 5.3 Denenecek sorular

| Soru | Neyi gösterir? |
|---|---|
| "Salı günü hangi derslerim var, yemekte ne var?" | Tek seferde **iki araç** çağırma |
| "Yarın ne yiyeceğim?" | LLM'in "yarın"ı araca doğru parametre olarak geçirmesi |
| "Algoritmalar için cuma gününe graf ödevi ekle" | LLM'in tarihi kendisi hesaplaması (sistem mesajında bugünün tarihi var) |
| Ardından: "Onu tamamla" | **Hafıza**: "onu" kelimesini önceki konuşmadan çözmesi |
| "Bekleyen ödevlerimden en acil olanı hangisi?" | Araç sonucunu yorumlayıp akıl yürütmesi |
| "Bana bir fıkra anlat" | Araç **gerekmediğinde** doğrudan cevap vermesi |

Bunları `--protokol` ile çalıştırıp her adımda hangi MCP mesajının gittiğini izle.

---

## 6. Bölüm 3: Veritabanına geçiş (PostgreSQL + Docker)

**Dosyalar:** `docker-compose.yml`, `postgres/init.sql`, `server_postgres.py`, `postgres_test.py`

Gerçek hayatta veriler genelde bir veritabanında durur. Bu bölümde aynı ajanı, PostgreSQL'den
veri getiren ikinci bir MCP server'a bağlayacağız.

### 6.1 Veritabanını başlat

Proje klasöründe:

```bash
docker compose up -d
docker compose ps
```

`STATUS` sütununda **`healthy`** yazana kadar birkaç saniye bekle.

| Ayar | Değer |
|---|---|
| Adres | `localhost:5433` (5432 değil; bilgisayarındaki başka bir PostgreSQL ile çakışmasın diye) |
| Veritabanı / kullanıcı / şifre | `kampus` / `kampus` / `kampus123` |
| Salt okunur ajan kullanıcısı | `ajan_okuyucu` / `okuyucu123` |

### 6.2 Tablolar ve veriler nasıl oluştu?

Hiçbir SQL komutu çalıştırmadın, ama tablolar ve veriler hazır. Bunu Docker yapıyor:
`docker-compose.yml` içindeki şu satır, `init.sql`'i konteynerin özel bir klasörüne bağlar:

```yaml
- ./postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro
```

PostgreSQL imajı, veritabanını **ilk kez** oluştururken bu klasördeki `.sql` dosyalarını
otomatik çalıştırır. Gerçekleştiğini loglarda görebilirsin:

```bash
docker compose logs postgres
```

```
running /docker-entrypoint-initdb.d/01-init.sql
CREATE TABLE        ← dersler
CREATE TABLE        ← ders_programi
CREATE TABLE        ← menu
CREATE TABLE        ← odevler
INSERT 0 4          ← 4 ders eklendi
INSERT 0 7          ← 7 ders saati
INSERT 0 20         ← 20 menü kalemi
INSERT 0 5          ← 5 ödev
CREATE ROLE         ← ajan_okuyucu kullanıcısı
GRANT ...
```

> ⚠️ `init.sql` **sadece veritabanı boşken** çalışır. Dosyayı değiştirdikten sonra
> değişikliklerin uygulanması için veritabanını sıfırlaman gerekir:
> `docker compose down -v` ve ardından `docker compose up -d`

**Şema:**

```
dersler (id, ad, ogretim_uyesi, kredi)
   ▲             ▲
   │ ders_id     │ ders_id
ders_programi    odevler (id, ders_id, aciklama, son_tarih, tamamlandi, eklenme)
(id, ders_id, gun, saat, derslik)

menu (id, gun, sira, yemek, kalori)
```

### 6.3 Verilere kendin bak

```bash
docker exec -it kampus-postgres psql -U kampus
```

```sql
\dt                                       -- tabloları listele
\d odevler                                -- bir tablonun yapısı
SELECT * FROM dersler;
SELECT d.ad, o.aciklama, o.son_tarih
  FROM odevler o JOIN dersler d ON d.id = o.ders_id
  ORDER BY o.son_tarih;
\q                                        -- çık
```

### 6.4 Server'ı test et

```bash
python postgres_test.py
```

Tüm araçları, ajanın kullandığı yolla (MCP üzerinden) gerçek veritabanına karşı dener.
Sonunda `10 başarılı, 0 başarısız` görmelisin. Test sırasında eklenen ödev sonunda silinir.

### 6.5 Server'ın kodu: FastMCP

`server.py`'de protokolü elle yazmıştık. `server_postgres.py` ise resmi MCP SDK'sını kullanır.
Bir araç tanımlamak bu kadar:

```python
@mcp.tool()
def yemekhane_menusu(gun: GunParametresi = "bugün") -> str:
    """Kampüs yemekhanesinin belirtilen gündeki menüsünü kalori bilgisiyle getirir."""
    g = gun_coz(gun)
    satirlar = sorgula("SELECT yemek, kalori FROM menu WHERE gun = %s ORDER BY sira", (g,))
    ...
```

FastMCP; fonksiyon adını araç adına, docstring'i `description`'a, tip ipuçlarını
(`str`, `int`, `bool`) `inputSchema`'ya kendisi çevirir. `initialize`, `tools/list` ve
`tools/call` mesajlarını da kendisi yönetir. İki dosyayı yan yana açıp karşılaştır.

> **SQL enjeksiyonuna dikkat:** Parametreler SQL'e asla string birleştirme ile eklenmez.
> `%s` yer tutucusu ve ayrı bir parametre listesi kullanılır; kaçış işlemini veritabanı
> sürücüsü yapar.

### 6.6 Ajanı veritabanına bağla

```bash
python ajan.py --kaynak postgres --protokol
```

`--protokol` ile her adımda server'ın veritabanına gönderdiği **gerçek SQL**'i de görürsün.
(FastMCP, `server.py`'deki ⬅️/➡️ mesaj loglarını göstermez; onların yerine 🔧 ve 🐘 satırlarını görürsün.)

```
🧠 [ajan] Araç çağırmaya karar verdi → yemekhane_menusu({'gun': 'salı'})
[20:16:27] 🔧 yemekhane_menusu(gun='salı')
[20:16:27] 🐘 SQL: SELECT yemek, kalori FROM menu WHERE gun = %s ORDER BY sira  ['salı']
[20:16:27] 🐘 4 satır döndü (3.2 ms)
🔧 [araclar] yemekhane_menusu sonucu: Salı menüsü: Ezogelin çorbası (170 kcal), ...
```

**Denenecek sorular:**

| Soru | Neyi gösterir? |
|---|---|
| "Salı yemekte ne var, kaç kalori?" | Aynı araç, artık veritabanından ve daha zengin veriyle |
| "Çarşamba derslerim neler, hocaları kim?" | JOIN ile gelen öğretim üyesi bilgisi |
| "Veritabanı Sistemleri için 20 Ekim'e normalizasyon ödevi ekle" | Veritabanına **yazma** |
| "Fizik dersi için ödev ekle" | Olmayan ders → hata → ajan mevcut dersleri öğrenir |
| "Hangi derste kaç ödevim var?" | Hazır araç yok → ajan **kendi SQL'ini yazar** (`sql_sorgula`) |
| "Toplam kaç kredilik ders alıyorum?" | `sql_sorgula` ile toplama |
| "Haftanın en kalorili günü hangisi?" | İpucu: iki gün berabere. Ajan bunu fark edebilecek mi? |

Ajan bir ödev eklediyse, değişikliği veritabanında doğrula:
```bash
docker exec -it kampus-postgres psql -U kampus -c "SELECT * FROM odevler ORDER BY id"
```

### 6.7 Güvenlik: LLM'in yazdığı SQL'e güvenme

`sql_sorgula`, LLM'in ürettiği SQL'i çalıştırır. Kötü niyetli biri ajanı "tüm ödevleri sil"
diye kandırmaya çalışabilir (**prompt injection**). Bu yüzden üç katmanlı bir savunma var:

| Katman | Nerede | Ne yapar |
|---|---|---|
| 1. Kod kontrolü | `sql_sorgula()` | Sorgu `SELECT`/`WITH` ile başlamalı, tek sorgu olmalı |
| 2. Salt okunur kullanıcı | `init.sql` | `ajan_okuyucu` sadece `SELECT` yetkisine sahip |
| 3. READ ONLY bağlantı | `sorgula()` | Bağlantı salt okunur; sorgulara 5 saniye süre sınırı |

**Birinci katman neden tek başına yetmez?** Şu sorgu `WITH` ile başladığı için kod kontrolünü
geçer, ama aslında bir silme işlemidir:

```sql
WITH s AS (DELETE FROM odevler RETURNING *) SELECT count(*) FROM s
```

Bunu 2. ve 3. katman durdurur (`postgres_test.py` bunu da test eder).
**Ders:** Güvenliği tek bir kontrole bırakma; en güçlü katman veritabanı yetkileridir
("en az yetki ilkesi").

### 6.8 İşin bitince

```bash
docker compose down        # Konteyneri durdur (veriler korunur)
docker compose down -v     # Konteyneri durdur ve verileri sil (sonraki açılışta init.sql tekrar çalışır)
```

---

## 7. Bonus: Claude Desktop'a bağlamak

Yazdığın server'lar MCP standardına uyduğu için başka istemcilerle de çalışır.
Claude Desktop'ta **Ayarlar → Geliştirici → Yapılandırmayı düzenle** yolunu izle ve
`claude_desktop_config.json` dosyasına şunu ekle (yolları kendi bilgisayarına göre değiştir):

```json
{
  "mcpServers": {
    "kampus-dosya": {
      "command": "C:\\Users\\ad\\kampus-asistani\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\ad\\kampus-asistani\\server.py"]
    },
    "kampus-postgres": {
      "command": "C:\\Users\\ad\\kampus-asistani\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\ad\\kampus-asistani\\server_postgres.py"]
    }
  }
}
```

macOS/Linux'ta `command` için `/Users/ad/kampus-asistani/.venv/bin/python` gibi bir yol kullan.
`command` olarak sanal ortamdaki Python'u vermek önemli; `server_postgres.py`'nin ihtiyaç
duyduğu paketler orada kurulu. Claude Desktop'ı yeniden başlattıktan sonra
"Yarın hangi derslerim var?" diye sorabilirsin.

---

## 8. Alıştırmalar

Kolaydan zora sıralı. Her alıştırmadan sonra ajanı `--protokol` ile çalıştırıp yeni aracının
gerçekten çağrıldığını gör.

### Alıştırma 1: `server.py`'ye araç ekle ⭐

Kütüphanenin açık olduğu saatleri döndüren bir araç ekle.

1. Fonksiyonu yaz (diğer araç fonksiyonlarının yanına):
   ```python
   def kutuphane_saatleri(gun="bugün"):
       g = gun_coz(gun)
       return "Kapalı" if g == "pazar" else f"{g.capitalize()}: 08:00 - 22:00"
   ```
2. `ARACLAR` sözlüğüne tanıt:
   ```python
   "kutuphane_saatleri": {
       "fonksiyon": kutuphane_saatleri,
       "description": "Kütüphanenin belirtilen gündeki açık olduğu saatleri getirir.",
       "inputSchema": {"type": "object", "properties": {"gun": GUN_PARAMETRESI}},
   },
   ```
3. Test et: `python ajan.py --soru "Pazar günü kütüphane açık mı?"`

### Alıştırma 2: Aynı aracı FastMCP ile ekle ⭐

Aynı aracı `server_postgres.py`'ye ekle (`if __name__ == "__main__":` satırının üstüne):

```python
@mcp.tool()
def kutuphane_saatleri(gun: GunParametresi = "bugün") -> str:
    """Kütüphanenin belirtilen gündeki açık olduğu saatleri getirir."""
    g = gun_coz(gun)
    return "Kapalı" if g == "pazar" else f"{g.capitalize()}: 08:00 - 22:00"
```

Alıştırma 1 ile karşılaştır: hangisi daha az kod? `ARACLAR` sözlüğündeki bilgiler burada
nereden geliyor?

### Alıştırma 3: Kötü açıklama deneyi ⭐⭐

`yemekhane_menusu` aracının açıklamasını `"Veri getirir."` yap ve "Yarın ne yiyeceğim?" diye sor.
Ajan doğru aracı bulabiliyor mu? Sonra açıklamayı geri al. Bu deney, açıklamanın neden
kod kadar önemli olduğunu gösterir.

### Alıştırma 4: Sınav takvimi tablosu ⭐⭐

1. `postgres/init.sql`'e bir `sinavlar` tablosu ekle (ders_id, tarih, saat, derslik) ve
   birkaç satır veri gir.
2. Veritabanını sıfırla: `docker compose down -v && docker compose up -d`
3. `server_postgres.py`'ye `sinav_takvimi()` aracını ekle.
4. `sql_sorgula`'nın açıklamasındaki şemaya da yeni tabloyu yaz (yoksa ajan bu tabloyu bilmez).
5. Sor: "Önümüzdeki sınavlarım ne zaman?"

### Alıştırma 5: Ajana kişilik ver ⭐⭐

`ajan.py` içindeki `sistem_mesaji()` fonksiyonunu değiştir. Örneğin ajan her cevabın sonunda
bir motivasyon cümlesi söylesin ya da cevaplarını hep madde madde versin.

### Alıştırma 6: İnsan onayı ⭐⭐⭐

Ajan `odev_ekle` veya `odev_tamamla` çağırmadan önce kullanıcıya "Onaylıyor musun? (e/h)"
diye sorsun. İpucu: LangGraph'ta `interrupt` kavramını veya `araclar` düğümünden önce
çalışan yeni bir düğüm eklemeyi araştır. Gerçek dünyadaki ajanlarda veriyi değiştiren her
işlem için bu tür bir onay adımı çok önemlidir.

### Alıştırma 7: İki server'a birden bağlan ⭐⭐⭐

`MultiServerMCPClient` birden fazla server'ı aynı anda kabul eder. Ajanı hem `server.py`'ye
hem `server_postgres.py`'ye bağla. Aynı adlı araçlar çakışacak; `tool_name_prefix=True`
parametresini araştır.

---

## 9. Sorun giderme

| Belirti | Olası neden ve çözüm |
|---|---|
| `ModuleNotFoundError: No module named 'langgraph'` | Sanal ortam etkin değil. `.venv\Scripts\Activate.ps1` veya `source .venv/bin/activate`, sonra `pip install -r requirements.txt` |
| `ConnectError: All connection attempts failed` | Ollama çalışmıyor. `ollama serve` ile başlat ya da Ollama uygulamasını aç. |
| `model "qwen2.5:7b" not found` | Model indirilmemiş: `ollama pull qwen2.5:7b` |
| Ajan araçları hiç kullanmıyor, cevap uyduruyor | Küçük modeller araç çağırmada zorlanabilir. `qwen2.5:7b` veya daha büyük bir model dene. |
| PowerShell: "running scripts is disabled" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` çalıştır, sonra tekrar etkinleştir. |
| Türkçe karakterler bozuk görünüyor (Windows) | Windows Terminal kullan veya terminalde `chcp 65001` çalıştır. |
| `Veritabanına bağlanılamadı` | Docker Desktop açık mı? `docker compose ps` ile konteynerin `healthy` olduğunu kontrol et. |
| `port is already allocated` | 5433 portu dolu. `docker-compose.yml`'de `"5434:5432"` yap ve `KAMPUS_DB` ortam değişkeniyle bağlantı adresini güncelle (bkz. aşağıdaki tablo). |
| `init.sql`'deki değişiklikler görünmüyor | `init.sql` sadece ilk açılışta çalışır: `docker compose down -v && docker compose up -d` |
| Loglarda `Skipping initialization` | Volume zaten dolu; yukarıdaki sıfırlama komutunu kullan. |
| `python server.py` sadece bekliyor | Normal; server agent'tan mesaj bekliyor. Bkz. [4.2](#42-server-ile-elle-konuş). |

**Ortam değişkenleri:**

| Değişken | Varsayılan | Ne işe yarar |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:7b` | Kullanılacak Ollama modeli |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama'nın adresi (başka bilgisayardaysa) |
| `ANTHROPIC_API_KEY` | — | `--model anthropic` için |
| `KAMPUS_LOG` | `detay` | `sessiz` yaparsan server sadece hataları loglar |
| `KAMPUS_DB` | `postgresql://kampus:kampus123@localhost:5433/kampus` | Yazma yetkili bağlantı |
| `KAMPUS_DB_OKUYUCU` | `postgresql://ajan_okuyucu:okuyucu123@localhost:5433/kampus` | `sql_sorgula` bağlantısı |

---

## 10. Komut özeti

```bash
# --- Kurulum (bir kez) ---
python -m venv .venv; .venv\Scripts\Activate.ps1       # Windows (PowerShell)
python3 -m venv .venv && source .venv/bin/activate      # macOS / Linux
pip install -r requirements.txt
ollama pull qwen2.5:7b

# --- Bölüm 1: MCP server'ı elle ---
python server.py

# --- Bölüm 2: Ajan (dosya kaynağı) ---
python ajan.py
python ajan.py --protokol
python ajan.py --graf

# --- Bölüm 3: PostgreSQL ---
docker compose up -d
docker compose ps
docker compose logs postgres
python postgres_test.py
python ajan.py --kaynak postgres --protokol
docker exec -it kampus-postgres psql -U kampus
docker compose down -v
```

---

## 11. Ek: Eğitmen notları

### Etkinlikten önce (internet varken)

- [ ] **Python paketleri:** Öğrencilerin interneti olmayacaksa paketleri önceden indirip
  USB ile dağıt. Bazı paketler (örn. `pydantic-core`, `psycopg-binary`) işletim sistemine özel
  derlendiği için **her işletim sistemi için ayrı** indirilmeli:
  ```bash
  pip download -r requirements.txt -d paketler        # her işletim sisteminde ayrı ayrı
  pip install --no-index --find-links paketler -r requirements.txt   # öğrenci tarafında
  ```
- [ ] **Docker imajı:**
  ```bash
  docker pull postgres:17-alpine
  docker save postgres:17-alpine -o postgres-17-alpine.tar   # USB'ye kopyala
  docker load -i postgres-17-alpine.tar                       # öğrenci tarafında
  ```
- [ ] **Ollama modeli:** `ollama pull qwen2.5:7b` (yedek olarak `qwen2.5:3b`)
- [ ] **Uçtan uca prova:** `python postgres_test.py` → 10/10, ardından Bölüm 5.3 ve 6.6'daki
  soruları kendi makinende dene.
- [ ] **Veritabanını sıfırla:** Provadan sonra ajanın eklediği ödevler kalmasın diye
  `docker compose down -v && docker compose up -d`. JSON tarafında `veri/odevler.json`
  dosyasının orijinal halini yedekle.

### Ollama'yı tek makineden paylaşmak

Model yaklaşık 4.7 GB ve her laptop kaldıramaz. Ollama'yı kendi bilgisayarında çalıştırıp
öğrencileri internetsiz bir yerel ağdan (telefon hotspot'u yeterli) bağlayabilirsin:

```bash
# Eğitmen (macOS / Linux)
OLLAMA_HOST=0.0.0.0 ollama serve
# Eğitmen (Windows PowerShell): Ollama'yı kapat, sonra
$env:OLLAMA_HOST="0.0.0.0"; ollama serve

# Öğrenci (macOS / Linux)
OLLAMA_HOST=http://<eğitmenin-ip-adresi>:11434 python ajan.py
# Öğrenci (Windows PowerShell)
$env:OLLAMA_HOST="http://<eğitmenin-ip-adresi>:11434"; python ajan.py
```

Eğitmen bilgisayarında güvenlik duvarının 11434 portuna izin verdiğinden emin ol.
Kalabalık bir sınıfta tek makine yavaşlayabilir; tüm sınıfın aynı anda soru sormasındansa
grup çalışması daha iyi sonuç verir.

### Önerilen demo akışı

| # | Ne gösterilir | Komut | Ana mesaj |
|---|---|---|---|
| 1 | MCP'nin özü | `python server.py` + elle JSON | "MCP sadece satır satır JSON'dur" |
| 2 | Ajan karar veriyor | `python ajan.py --protokol` | "LLM aracı seçer, MCP taşır" |
| 3 | Hata ve düzeltme | "...cuma'ya ödev ekle" | "`isError` sayesinde ajan kendini düzeltir" |
| 4 | Hafıza | "Onu tamamla" | "Checkpointer sohbeti hatırlar" |
| 5 | Kaynak değişimi | `--kaynak postgres --protokol` | "Server değişti, ajan aynı" |
| 6 | SQL üreten ajan | "Hangi derste kaç ödevim var?" | "Ajan kendi sorgusunu yazar" |
| 7 | Güvenlik | Bölüm 6.7 | "LLM'e asla tam yetki verme" |

### Tartışma soruları

- Ajan bir aracı yanlış zamanda çağırırsa kimin hatasıdır: modelin mi, açıklamanın mı?
- `sql_sorgula` gibi esnek bir araç mı, yoksa her soru için ayrı ve dar araçlar mı daha iyi?
  Güvenlik, esneklik ve güvenilirlik açısından tartışın.
- Bir e-posta gönderen MCP aracı yazsaydık hangi güvenlik önlemlerini alırdık?
- Ajanın eklediği bir ödevi kullanıcı onaylamadan kaydetmesi doğru mu? (Bkz. Alıştırma 6)

---

## Daha fazla bilgi

- MCP resmi sitesi ve spesifikasyonu: [modelcontextprotocol.io](https://modelcontextprotocol.io)
- MCP Python SDK (FastMCP): [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)
- LangGraph: [github.com/langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)
- LangChain MCP adaptörleri: [github.com/langchain-ai/langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
- Ollama modelleri: [ollama.com/library](https://ollama.com/library)
