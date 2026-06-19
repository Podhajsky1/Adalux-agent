# ADALUX Meeting Agent v2

Autonomní telefonní agent pro domlouvání obchodních schůzek s obcemi.
Kontakty z vlastní databáze obcí — žádné vyhledávání před hovorem.

---

## Co je nové ve v2

- **Žádný web search** – kontakty (telefon, email, kraj, počet obyvatel) jsou
  v lokální SQLite databázi vytvořené z tvého Excelu se 6 393 obcemi.
- **Volá na úřad, ne přímo starostovi** – agent se hned představí jako
  výrobce a žádá o přepojení. Nikdy nezakrývá účel hovoru.
- **Haiku 4.5 pro vedení hovoru** – cca 15× levnější než Sonnet, pro
  strukturovaný scénář plně dostačující.
- **Editovatelný skript hovoru** – `prompts/system_prompt.txt`, lze upravovat
  přímo v dashboardu bez nového nasazení.
- **Stav kampaně je perzistentní** – jednou zavolaná obec se podruhé
  nevolá (kromě „zpětné volání", to se zkusí znovu automaticky).
- **Plný přepis hovoru** se zapisuje do Excelu (poslední sloupec) — pro
  zpětné ladění skriptu podle reálných reakcí.

---

## Instalace

### 1. Příprava databáze (JEDNORÁZOVĚ, lokálně)

```bash
pip install -r requirements.txt
python build_database.py "Databáze_městských_a_obecních_úřadů_České_republiky_2025.xls"
```

Vytvoří `data/municipalities.db` (SQLite, ~6 387 obcí s validním telefonem).
Tento soubor se nahraje na Railway spolu s kódem — žádné API volání za běhu.

> Pokud později dostaneš novější verzi databáze, stačí skript spustit znovu —
> přepíše tabulku, ale ZACHOVÁ stav předchozích kampaní jen pokud necháš
> stávající `data/municipalities.db` a budeš dělat UPDATE místo DROP.
> (Pro běžné použití – spusť jen jednou na začátku.)

### 2. Twilio

Stejné jako dříve – buď ověř svůj mobil (Verified Caller ID, zdarma),
nebo kup české číslo. Podrobný návod viz `setup-guide.md` z předchozí zprávy.

### 3. Anthropic API klíč

Stávající klíč funguje. Žádné nové oprávnění není potřeba.

### 4. .env soubor

Zkopíruj `.env.example` na `.env`, vyplň hodnoty.

### 5. Railway deploy

```bash
railway login
railway init
railway up
```

**Důležité:** `data/municipalities.db` musí být součástí nahrávaného kódu
(není v `.gitignore`), jinak server na Railway nebude mít kontakty.

Nastav `BASE_URL` v Railway Variables na vygenerovanou doménu.

---

## Použití

### Dashboard
`https://tvoje-url.up.railway.app`

- **Stav databáze** – kolik obcí čeká, kolik je hotovo, kolik má zpětné volání
- **Spustit kampáň** – zadáš počet obcí, produkt, volitelně kraj/velikost obce
- **Skript hovoru** – textové pole, kde upravuješ chování agenta naživo
- **Log hovorů** – tabulka všech hovorů + stažení Excelu

### Spuštění kampaně přes API

```bash
# 20 obcí v Moravskoslezském kraji, jen obce nad 1000 obyvatel
curl -X POST https://tvoje-url.up.railway.app/campaign/start \
  -H "Content-Type: application/json" \
  -d '{
    "count": 20,
    "product": "lighting",
    "kraj": "Moravskoslezský",
    "min_population": 1000
  }'
```

### Jeden testovací hovor (na sebe)

Nejdřív přidej svou obec do databáze testovacím skriptem, nebo zavolej
existující malou obec a sleduj v Railway Logs, jak hovor probíhá.

```bash
curl -X POST https://tvoje-url.up.railway.app/call/single \
  -d '{"municipality": "Opava", "product": "lighting"}'
```

---

## Ladění skriptu hovoru

1. Spusť 5–10 testovacích hovorů (malá dávka, např. `count: 5`)
2. Stáhni Excel log – přečti si sloupec **Přepis hovoru**
3. V dashboardu uprav textové pole **Skript hovoru hovoru**, ulož
4. Další dávka – porovnej výsledky

Soubor `prompts/system_prompt.txt` obsahuje celý scénář: úvod, zvládání
sekretářky, hlavní argumenty, námitky, cíl hovoru. Lze měnit cokoliv —
formát JSON odpovědi na konci souboru ale neměň, na něm závisí zpracování.

---

## Stavy obce v databázi

| call_status | Co znamená | Zavolá se znovu? |
|---|---|---|
| `pending` | Ještě nezavoláno (nebo bylo nedostupné/záznamník) | Ano |
| `done` | Schůzka domluvena / nezájem / poslán email | Ne |
| `callback` | Požádali o zpětné volání v konkrétní den | Ano, po `callback_date` |

Reset jednotlivé obce (např. po manuální chybě):
```bash
curl -X POST https://tvoje-url.up.railway.app/municipality/reset/123
```
(123 = id obce, najdeš v Excel logu nebo v databázi)

---

## Náklady (odhad, 100 hovorů/den)

| Položka | Cena |
|---|---|
| Twilio (ověřené číslo, ~1,8 Kč/min, 3 min/hovor) | ~540 Kč/den |
| Claude Haiku (vedení hovoru, ~10 kol) | ~15 Kč/den |
| Railway | $5–20/měsíc |
| **Celkem/den** | **~555 Kč** |
| **Celkem/měsíc (22 prac. dní)** | **~12 200 Kč** |

**Pro výrazné snížení nákladů** zvaž SIP trunk (české sazby ~0,2 Kč/min
místo Twilio 1,8 Kč/min) — sníží náklady na hovory cca 8×, výsledné
měsíční náklady by klesly na ~1 800–2 500 Kč. Dej vědět, pokud chceš
tuto úpravu zapracovat.
