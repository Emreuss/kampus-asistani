"""
Kampüs Asistanı Ajanı: LangGraph + LangChain + MCP

Mimari:
    Kullanıcı ──> [ ajan ] ──(araç lazım mı?)──> [ araclar ] ──┐
                     ^                                         │
                     └─────────────── sonuçlar ────────────────┘
                     │
                     └──(araç gerekmiyor)──> Cevap

- Araçları BİZ yazmıyoruz: langchain-mcp-adapters, server.py'deki MCP
  araçlarını otomatik olarak LangChain araçlarına dönüştürüyor.
- Grafiği BİZ kuruyoruz: LangGraph ile iki düğüm ve bir koşullu kenar.

Kullanım:
    python ajan.py                          # Ollama ile sohbet (internetsiz)
    python ajan.py --model anthropic        # Claude ile (internet + ANTHROPIC_API_KEY)
    python ajan.py --soru "Yarın derslerim neler?"   # Tek soru sor ve çık
    python ajan.py --graf                   # Grafiğin Mermaid çizimini yazdır
    python ajan.py --protokol               # Server'daki MCP trafiğini de canlı göster
    python ajan.py --kaynak postgres        # Dosyalar yerine PostgreSQL MCP server'ına bağlan
"""

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

sys.stdout.reconfigure(encoding="utf-8")
if sys.platform == "win32":
    os.system("")  # Windows terminalinde renkleri etkinleştirir

MAVI, SARI, YESIL, GRI, SIFIRLA = "\033[94m", "\033[93m", "\033[92m", "\033[90m", "\033[0m"
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def metne_cevir(icerik):
    """Mesaj içeriği bazen düz metin, bazen içerik blokları listesi olarak gelir."""
    if isinstance(icerik, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in icerik)
    return str(icerik)


# =====================================================================
# 1) MODEL: Ajanın "beyni". Hangisini kullanacağımızı seçiyoruz.
# =====================================================================

def model_sec(saglayici, model_adi=None):
    if saglayici == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model_adi or os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
            # Ollama başka bir bilgisayarda çalışıyorsa (örn. hocanın makinesi):
            # OLLAMA_HOST=http://192.168.1.10:11434 python ajan.py
            base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            temperature=0,
        )
    if saglayici == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_adi or "claude-haiku-5-5", temperature=0)
    raise ValueError(f"Bilinmeyen model sağlayıcı: {saglayici}")


def sistem_mesaji():
    # LLM bugünün tarihini bilmez; "yarın" veya "cuma" gibi ifadeleri
    # tarihe çevirebilmesi için ona söylüyoruz.
    bugun = date.today()
    return SystemMessage(content=(
        "Sen bir üniversite öğrencisine yardım eden Kampüs Asistanı'sın. "
        f"Bugün {bugun.isoformat()}, {GUNLER[bugun.weekday()]}. "
        "Menü, ders programı ve ödevlerle ilgili sorularda MUTLAKA araçları kullan, "
        "bilgi uydurma. Hazır araçlarla cevaplanamayan sorular (istatistik, sayma, karşılaştırma) "
        "için sql_sorgula aracın varsa onu kullan. Kısa ve samimi Türkçe cevaplar ver."
    ))


# =====================================================================
# 2) GRAF: LangGraph ile ajanın düşünme döngüsünü kuruyoruz.
# =====================================================================

def graf_olustur(model, araclar):
    model_araclarla = model.bind_tools(araclar)  # Modele "bu araçların var" diyoruz.

    async def ajan(durum: MessagesState):
        """Düğüm 1: Model mesajlara bakar; ya araç çağırmaya karar verir ya da cevap yazar."""
        cevap = await model_araclarla.ainvoke([sistem_mesaji()] + durum["messages"])
        return {"messages": [cevap]}

    graf = StateGraph(MessagesState)
    graf.add_node("ajan", ajan)
    graf.add_node("araclar", ToolNode(araclar))  # Düğüm 2: İstenen araçları MCP üzerinden çalıştırır.

    graf.add_edge(START, "ajan")
    # tools_condition: Son mesajda araç çağrısı varsa "araclar"a, yoksa bitişe git.
    graf.add_conditional_edges("ajan", tools_condition, {"tools": "araclar", "__end__": "__end__"})
    graf.add_edge("araclar", "ajan")  # Araç sonuçlarıyla tekrar ajana dön.

    # InMemorySaver: Sohbet geçmişini hatırlar ("onu tamamla" gibi takip sorularına izin verir).
    return graf.compile(checkpointer=InMemorySaver())


# =====================================================================
# 3) ÇALIŞTIRMA: Grafı adım adım akıtıp her adımı ekrana yazıyoruz.
# =====================================================================

async def sor(graf, soru, oturum_id="ders"):
    ayar = {"configurable": {"thread_id": oturum_id}}
    async for adim in graf.astream({"messages": [("user", soru)]}, ayar, stream_mode="updates"):
        for dugum, guncelleme in adim.items():
            for mesaj in guncelleme["messages"]:
                if isinstance(mesaj, AIMessage) and mesaj.tool_calls:
                    for c in mesaj.tool_calls:
                        print(f"{SARI}🧠 [{dugum}] Araç çağırmaya karar verdi → {c['name']}({c['args']}){SIFIRLA}")
                elif isinstance(mesaj, ToolMessage):
                    print(f"{GRI}🔧 [{dugum}] {mesaj.name} sonucu: {metne_cevir(mesaj.content)}{SIFIRLA}")
                elif isinstance(mesaj, AIMessage):
                    print(f"{YESIL}🤖 Asistan:{SIFIRLA} {metne_cevir(mesaj.content)}")


async def guvenli_sor(graf, soru, model):
    """Sınıfta en sık karşılaşılan hatalar için anlaşılır mesajlar gösterir."""
    try:
        await sor(graf, soru)
    except Exception as hata:
        print(f"\n❌ Hata: {type(hata).__name__}: {hata}")
        if model == "ollama":
            print("💡 Ollama çalışıyor mu? Terminalde 'ollama serve' komutunu çalıştır.\n"
                  "   Model indirildi mi? 'ollama list' ile kontrol et, yoksa 'ollama pull qwen2.5:7b'.")
        elif model == "anthropic":
            print("💡 İnternet bağlantını ve ANTHROPIC_API_KEY ortam değişkenini kontrol et.")


async def main():
    p = argparse.ArgumentParser(description="Kampüs Asistanı Ajanı")
    p.add_argument("--model", default="ollama", choices=["ollama", "anthropic"])
    p.add_argument("--model-adi", help="Örn. qwen2.5:7b, llama3.1:8b")
    p.add_argument("--soru", help="Tek bir soru sor ve çık")
    p.add_argument("--graf", action="store_true", help="Grafın Mermaid çizimini yazdır")
    p.add_argument("--protokol", action="store_true", help="Server'ın gelen/giden MCP mesaj loglarını da göster")
    p.add_argument("--kaynak", default="dosya", choices=["dosya", "postgres"],
                   help="Hangi MCP server'a bağlanılacak: JSON dosyaları veya PostgreSQL")
    a = p.parse_args()

    # Ajan kodu iki kaynakta da AYNI. Değişen tek şey hangi server'ın başlatıldığı.
    server_dosyasi = {"dosya": "server.py", "postgres": "server_postgres.py"}[a.kaynak]

    # MCP server'a bağlan ve araçları otomatik olarak al.
    istemci = MultiServerMCPClient({
        "kampus": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(Path(__file__).parent / server_dosyasi)],
            # Server logları varsayılan olarak kapalı; --protokol ile açılır.
            # os.environ'u da aktarıyoruz, yoksa server saat dilimi gibi ayarları göremez.
            "env": {**os.environ, "KAMPUS_LOG": "detay" if a.protokol else "sessiz"},
        }
    })

    # Kalıcı oturum: Server'ı bir kez başlatıp tüm sohbet boyunca açık tutuyoruz.
    # (istemci.get_tools() kullansaydık her araç çağrısında server yeniden başlatılırdı.)
    async with istemci.session("kampus") as oturum:
        araclar = await load_mcp_tools(oturum)
        print(f"{MAVI}🔌 [{server_dosyasi}] {len(araclar)} araç yüklendi: {', '.join(t.name for t in araclar)}{SIFIRLA}")

        graf = graf_olustur(model_sec(a.model, a.model_adi), araclar)

        if a.graf:
            print(graf.get_graph().draw_mermaid())
            return
        if a.soru:
            await guvenli_sor(graf, a.soru, a.model)
            return

        print(f"Model: {a.model}. Çıkmak için 'q' yaz.\n")
        while True:
            # input() beklerken arka plandaki MCP bağlantısı donmasın diye ayrı thread'de soruyoruz.
            soru = (await asyncio.to_thread(input, f"{MAVI}👤 Sen:{SIFIRLA} ")).strip()
            if soru.lower() in ("q", "çık", "exit"):
                break
            if soru:
                await guvenli_sor(graf, soru, a.model)
                print()


if __name__ == "__main__":
    asyncio.run(main())
