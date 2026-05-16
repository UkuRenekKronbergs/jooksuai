# Struktureeritud intervjuu — Vorm.ai kasutajauuring

**Projekti plaan §4 punkt 4.** Kvalitatiivne hindamine kahe treeningkaaslasega
projekti lõpus (~30 minutit per intervjuu).

---

## Eesmärk ja ulatus

Hindame **kvalitatiivselt** kolme aspekti:

1. **Usaldusväärsus** — kas soovitus tundub mõistlik, kas põhjendus on usutav?
2. **Kasulikkus** — kas vastaja kasutaks seda enda treeninguks?
3. **Põhjenduse kvaliteet** — kas viited andmetele (ACWR, RPE) lisavad väärtust
   või on liigselt tehniline?

**Ei hinda:** üksiku soovituse õigsust, statistilist täpsust. Selleks on
projekti plaani §4.1 (retrospektiivne test) ja §4.2 (treener Ille Kukk).

---

## Intervjueeritavate kriteeriumid

Mõlemad treeningkaaslased peaksid vastama järgmistele:

- ✅ Aktiivne kesk-/pikamaajooksja (treenivad ≥ 3× nädalas, viimased ≥ 2 aastat)
- ✅ Kasutavad Stravat või Garmin Connecti
- ✅ Tunnevad enam-vähem ACWR, TRIMP, monotoonsuse mõisteid (vastasel juhul
  enne intervjuud 5-min seletav sissejuhatus)
- ✅ Pole osalenud arendusprotsessis ega näinud tööriista enne
- ❌ **Mitte** professionaalsed treenerid (see roll on Ille Kukel §4.2-s)

---

## Ettevalmistus enne intervjuud

Vajalik materjal (intervjuu juhi käes, mitte vastajatel):

1. **Sülearvuti** Vorm.ai jooksvas seisus (Streamlit dev-server, demo-andmestik
   laaditud).
2. **5–7 valitud reaalset päeva** mu enda 14-päevasest valideerimisperioodist
   (18.05–01.06), mille puhul on:
   - 2 päeva, kus mudel valis "Jätka plaanipäraselt" ja sportlane nõustus;
   - 1–2 päeva, kus mudel valis "Vähenda intensiivsust" või "Lisa
     taastumispäev" (ohutusreegel fire-s);
   - 1 päev, kus mudel ja treener Ille Kukk läksid lahku
     (vt valideerimisaruanne — lahkuminekute analüüs);
   - 1 päev, kus LLM andis "Alternatiivne treening" pehmel signaalil
     (mitte sunnitud reegliga) — illustreerib LLM-i lisaväärtust.
3. **Iga päeva kohta valmis ekraan/screenshot** koos:
   - ACWR-kõvera, päeva-koormuse, RPE-trendiga;
   - Soovituse kategooria + 2–4 lauseline põhjendus;
   - Subjektiivsed sisendid (RPE, uni).

---

## Intervjuu struktuur (30 min)

| Aeg | Tegevus |
|---|---|
| 0:00–0:03 | Eesmärgi tutvustus, nõusolek vastused projektis kasutada |
| 0:03–0:08 | Tööriista lühitutvustus + 1 demo-päev koos |
| 0:08–0:25 | 6 küsimust (allpool), 2–3 min vastusele |
| 0:25–0:30 | Avatud kommentaarid + tänulik lõpetus |

---

## 6 pool-struktureeritud küsimust

Iga küsimus on **avatud**, järelküsimusi tehakse vajadusel. Vastused logitakse
helisalvestusega (intervjueeritava nõusolekul) **JA** käsitsi märkmetena
allpool olevasse vormi.

### Küsimus 1 — Usaldusväärsus üldiselt

> "Vaata neid 3 päeva ja nende soovitusi. Kui suure tõenäosusega usaldaksid
> sellist soovitust enda treeningu kohta, kui see oleks sinu enda andmed?
> Hinda 1–5 skaalal ja põhjenda."

**Aluseks:** 3 lihtsamat päeva (1× "Jätka", 1× "Vähenda", 1× "Lisa taastumispäev").

**Selgitada vajadusel:**
- 1 = ei usaldaks üldse
- 3 = võib-olla, sõltub kontekstist
- 5 = kindlasti, järgiksin

---

### Küsimus 2 — Põhjenduse kvaliteet

> "Loe põhjendust selle päeva soovituse juures. Kas viited konkreetsetele
> arvudele (ACWR 1.45, RPE 8, uni 6.2 h) muudavad sinu meelest soovituse
> veenvamaks, või segavad need? Kas on midagi, mida sa eeldaksid, et seal
> peaks olema, aga pole?"

**Aluseks:** 1 päev, kus põhjendus sisaldab vähemalt 3 numbrit (ACWR + RPE +
uni või monotoonsus). Eelistatult mudeli "Alternatiivne treening" päev,
kus põhjendus on rikkalikum.

---

### Küsimus 3 — Lahkuminek treeneriga

> "Selle päeva puhul ütles mudel **X**, aga minu treener Ille Kukk ütles
> samade andmete põhjal **Y**. Loe mõlemat põhjendust. Kelle kommentaariga
> oled rohkem nõus ja miks? Kas oskaks öelda, millise infi puhul mudel
> eksis (kui üldse eksis)?"

**Aluseks:** üks päev valideerimisaruandest, kus treener ja mudel olid
eri arvamusel (näiteks 2026-05-22 — treener "Vähenda intensiivsust",
mudel "Jätka plaanipäraselt" pehme une-signaali tõttu).

**Eesmärk:** kas vastaja loomult eelistab mudeli ratsionaalsust või
treeneri intuitsiooni? Suure kõrvalekalde mustreid püüda.

---

### Küsimus 4 — Parim ja halvim näide

> "Vaata kõiki 5 päeva, mille üle me täna räägime. Vali nendest **üks parim**
> (kus soovitus tundub kõige paremini õigustatud) ja **üks halvim** (kus
> soovitus tundub kõige vähem õigustatud või veenev). Miks just need?"

**Eesmärk:** tuvastada konkreetsed mustrid, mis testijatele "klõpsavad"
versus mis tunduvad mehaanilised või liiga ettevaatlikud.

---

### Küsimus 5 — Enda kasutus

> "Kui sul oleks see tööriist holvis ühendatuna sinu enda Strava-kontoga,
> kas sa kasutaksid seda? Kui jah, siis kui sageli (iga päev / üle päeva /
> ainult kõvade nädalate ajal)? Mis tükid sind tagasi hoiaksid?"

**Järelküsimused:**
- "Kas tasuksid selle eest (€5–10/kuus, nagu TrainingPeaks)?"
- "Kas eelistaksid mobiilirakendust või veebivaadet?"

---

### Küsimus 6 — Mida muutuksid

> "Kui sa saaksid arendajalt küsida **ühte muudatust**, mis seda tööriista
> sinu jaoks oluliselt paremaks teeks, mis see oleks? See võib olla mistahes
> kohta — UI, soovitus, andmed, integratsioonid."

**Eesmärk:** prioriteerimissignaalid edaspidiseks. Üks idee, mitte loend.

---

## Vastuste vorm (1 leht per vastaja)

```
Vastaja:                ________________
Treeningstaaž (aastat): ________________
Kasutab Stravat?        Jah / Ei
Kasutab Garmini?        Jah / Ei
Kuupäev:                ________________

K1 (Usaldusväärsus 1–5):   ________________
   Põhjendus:              ____________________________________________

K2 (Põhjenduse kvaliteet):  ____________________________________________

K3 (Treeneriga lahkuminek): ____________________________________________

K4 (Parim/halvim näide):
   Parim:  ____________________________________________
   Halvim: ____________________________________________

K5 (Enda kasutus):   ____________________________________________
   Sagedus:          ____________________________________________
   Tasumine?:        Jah / Ei / Sõltub:  ____________________________

K6 (Üks muudatus):   ____________________________________________
```

---

## Vastuste käsitlus aruandes

Iga vastaja vastused **anonümiseerituna** (V1, V2) projekti lõpparuandes:

- K1 vastused → kvantitatiivne keskmine (4 mõõtmist: 2 vastajat × 2 päeva).
- K3 vastused → struktureeritud kvalitatiivne kokkuvõte (kelle poolele
  enamasti kalduvad, miks).
- K5 vastused → projekti edu kvalitatiivne kriteerium (§1: "kaks
  treeningkaaslast kinnitavad, et kasutaksid seda edaspidi").
- K6 vastused → edasiarenduse roadmap.

Helisalvestused **ei** läheks aruandesse. Hoian kohalikult 30 päeva, siis
kustutan.

---

## Etikett

- Selgitan, et **tööriist on otsustustugi**, mitte treener — eriti enne K3
  (treeneri vs mudeli võrdlus), et vastaja ei tunneks ennast keskel.
- Ei vaidle vastandlike vastustega. Märkin üles, jätkan järgmise küsimusega.
- Pärast intervjuud kirjutan vastajale tänumeili koos lubadusega tulemused
  saata.

---

## Versiooniajalugu

| Versioon | Kuupäev | Muudatus |
|---|---|---|
| 1.0 | 2026-05-18 | Esimene versioon, valmis 01.06-ks |
