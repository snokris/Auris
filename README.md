# Auris Studio — lokális hangoskönyvkészítő

Teljesen **lokális, ingyenes hangoskönyvkészítő**: EPUB-, PDF- vagy TXT-könyvből felolvasott hangoskönyvet készít (MP3/WAV + felirat), internetkapcsolat és API-kulcsok nélkül. A cél a **minél élethűbb, emberibb magyar felolvasás egyetlen narrátorhanggal** — automatikus nyelvfelismerés, magyar fejezetdetektálás, magyar számnévolvasás és hangklónozás magyar referenciahangból.

Ez a repó a [mp3pintyo/Auris](https://github.com/mp3pintyo/Auris) forkja, amely maga is az eredeti [nikhilprasanth/Auris](https://github.com/nikhilprasanth/Auris) projektre épül. A származási lánc: az **eredeti Auris** (nikhilprasanth) adja az OmniVoice-alapú hangoskönyvolvasó alapot; **mp3pintyo forkja** egészítette ki a Higgs TTS 3 motorral és a kiterjedt magyar nyelvi támogatással; az **Auris Studio** pedig mindezt Apple Silicon-támogatással, hangminőség-javításokkal, magyar szövegkezeléssel és egy végigvitt exportfolyamattal bővíti. Windows- és Linux-telepítéshez a mp3pintyo-repó útmutatója az irányadó.

## Képernyőképek

### Library
![Library](assets/library.png)

### Reader
![Reader](assets/reader.png)

### Voice Studio
![Voice Studio](assets/voice_studio.png)

### Settings
![Settings](assets/settings.png)

## Telepítés macOS-en (Apple Silicon)

Előfeltétel a [Homebrew](https://brew.sh), utána:

```bash
brew install python@3.12 ffmpeg git
git clone https://github.com/snokris/Auris-Studio.git
cd Auris-Studio
python3.12 -m venv reader/.venv
bash reader/setup.sh
```

Indítás:

```bash
bash reader/run.sh
```

Ezután a böngészőben: **http://127.0.0.1:7860**

Első használatkor a Settings oldalon töltsd le az OmniVoice-modellt (~3 GB), és állítsd az exportformátumot MP3-ra. A modellek betöltésekor a Terminálban ellenőrizhető az eszköz: `on mps (torch.float32)` (OmniVoice), illetve `device=mps, dtype=torch.bfloat16` (Higgs).

## Használat röviden

1. **Library** — EPUB/PDF/TXT importálása; a magyar nyelvet és a fejezethatárokat magától felismeri, a felismert fejezetek pedig import után szerkeszthetők.
2. **Reader** — lejátszás bármely mondattól; a fejezet hangja előre is legenerálható.
3. **Voice Studio** — narrátorhang beállítása leírással vagy klónozás referencia-WAV-ból (3–10 másodperces, tiszta, egybeszélős felvétel + pontos átirat); hangpresetek mentése, alkalmazása, valamint exportja és importja egyetlen `.aurisvoice` fájlként.
4. **Export** — a felső sávból nyíló panelen (`E` billentyű) fejezetek MP3-ba felirattal, `all`, `2-6` vagy `1,3,7-10` formában.

## Miben más az Auris Studio?

### Apple Silicon (MPS)

A TTS-motorok a Metal GPU-n futnak, nem CPU-n. Az OmniVoice float32-ben (a bfloat16 a mondatkezdeteket csípte le M-szérián), a Higgs a natív bfloat16-jában. Vészkapcsolók: `AURIS_STUDIO_MPS_DTYPE=bf16` (OmniVoice), `AURIS_STUDIO_HIGGS_MPS_DTYPE=fp32` (Higgs). Ez a rész PR-ként vissza is került az eredeti projektbe ([mp3pintyo/Auris#1](https://github.com/mp3pintyo/Auris/pull/1)).

### Egy narrátorhang, élethűen

Az Auris Studio teljes egészében **egynarrátoros felolvasásra** van hangolva: egyetlen, minél emberibb narrátorhang olvassa a teljes könyvet — leírásból tervezve vagy referencia-WAV-ból klónozva. A többszereplős narráció (karakterfelismerés, szereplőnkénti hangok) kódja megmaradt, de ki van kapcsolva; a kapcsoló az `app.py` `MULTI_VOICE_NARRATION` konstansa, a hozzá tartozó felületblokkok kikommentelve várakoznak (keresd: `MULTI_VOICE`).

### Hangminőség

A Whisper-illesztett szegmensvágás szóidőbélyegek és dinamikus programozásos szóillesztés alapján jelöli ki az összevontan generált hang szegmenshatárait; a vágás a szavak közti szünetbe, nullátmenetre kerül, élsimítással. Ezzel megszűntek a lecsípett mondatvégek, az áthallott szófoszlányok és a határkattanások. A Settingsben választható a vágási mód (`Split coalesced audio` → Aligned) és az illesztéshez használt Whisper-modell (alap: whisper-small).

### Természetes felolvasás

A valódi bekezdésvégeken a narrátor nagyobbat lélegzik: külön, hosszabb szünet szól (alap 0,85 mp, a Settingsben állítható), a lejátszásban, az exportban és a feliratban egyformán. Import közben a szöveg megtisztul a láthatatlan szeméttől (BOM, zero-width és vezérlőkarakterek, hibás Unicode), ami kiejtési hibákat és generálási bicsaklásokat okozna; az 500 karakternél hosszabb szövegegységek pedig tagmondathatáron feldarabolódnak, így a nagyon hosszú mondatok sem instabilak. Opcionálisan bekapcsolható a stúdiómasztering: finom EQ + kompresszor és kétmenetes EBU R128 hangosságillesztés (−19 LUFS) fejezetenként — hangoskönyv-szabvány, egyenletes hangerő.

### Magyar szövegkezelés

Magyar szövegnél a saját számnévolvasó fut le legelőször, még a motorok saját normalizálói előtt — azok ugyanis csak angolul, kínaiul és japánul tudnak, a `num2words` magyar tábláiban pedig a sorszámnevek száz fölött hibásak. A modul (`reader/core/hungarian_numbers.py`) kezeli a tő- és sorszámneveket, a dátumokat, a kötőjeles ragokat, az órákat, a százalékot, a hőfokot és a római számokat, és tudja, hogy csak az utolsó összetételi tag kapja a sorszámragot: a „932. esztendejében” így „kilencszázharminckettedik esztendejében”, nem pedig „kilencszázharminckettő”.

A fejezetfelismerés a kiírt sorszámneveket („Harmadik fejezet”) és a nevesített szakaszokat (Előszó, Epilógus) is fejezethatárnak veszi. A Higgs raw prompt módja szintén átmegy a normalizáláson, így ott sem maradnak számjegyek a felolvasásban.

### Fejezetszerkesztő és címnegyed

Import után a fejezetek átnevezhetők, kihagyhatók, törölhetők és összevonhatók. A címoldal és az impresszum alapból kimarad a felolvasásból, a mottó és a bevezető viszont bent marad, és az első fejezet előtti bevezető próza sem vész el.

### Exportfolyamat

Egyszerre egy export fut, Szünet / Folytatás / Leállítás vezérléssel; a szerver újraindítása után szüneteltetett állapotban tér vissza. Két folyamatjelző, mentett beállítások, könyvtárjelvény és élő állapotpont mutatja, hol tart. A fejezetek egyenként, elkészültükkor íródnak ki, így egy megszakadt export munkája nem vész el. Export közben a motorváltás, az újratöltés és a hangelőnézetek le vannak tiltva.

A kimenet lapos `exports/Szerző_-_Cím/` mappába kerül, és a fájlnevek is tartalmazzák a szerzőt — így a fájlok a mappájukból kiemelve is azonosíthatók:

```
exports/Dan_Brown_-_A_titkok_titka/
  Dan_Brown_-_A_titkok_titka_01_PROLÓGUS.mp3
  Dan_Brown_-_A_titkok_titka_01_PROLÓGUS.srt
```

Kérhető 1–4 összefűzött MP3 is a hozzájuk illeszkedő, újraidőzített felirattal. Az MP3-kódolás (VBR minőség vagy fix bitráta) és a beszédszünetek — mondatok, párbeszédfordulók, kihagyások, bekezdésvégek és fejezetek között — a Settingsben állíthatók. Minden MP3 ID3-címkéket kap: fejezetcím, szerző, könyvcím (album) és sorszám (track), így a fájlok a lejátszókban rendezetten jelennek meg.

### Hangcache és Voice Studio

A Beállítások hangcache-kártyája mutatja a cache méretét, kitakarítja az árva szegmenseket, és könyv törlésekor automatikusan söpör. A Voice Studióban a könyv referenciahangja (WAV + átirat) névvel elmenthető, bármely könyvre egy kattintással alkalmazható, és egyetlen `.aurisvoice` fájlba exportálható, illetve onnan visszatölthető — így a hang biztonsági mentése és gépek közti átvitele is egy fájl. Az akcentusválasztó „None (natural)” és „Hungarian” opcióval bővült — magyar felolvasáshoz az akcentusjelölés nélküli leírás ajánlott.

## TTS-motorok

| | OmniVoice (alapértelmezett) | Higgs TTS 3 — 4B |
|---|---|---|
| Magyar támogatás | igen (600+ nyelv) | igen, kiemelt |
| Erőssége | gyors, kis memóriaigény | kifejezőbb prozódia |
| Licenc | nyílt | kutatási/nem kereskedelmi* |

\* A Higgs licence hangoskönyveknél jól látható „Boson AI Higgs Audio” forrásmegjelölést kér, a hangklónozáshoz pedig a beszélő hozzájárulása szükséges. Részletek a [hivatalos modellkártyán](https://huggingface.co/bosonai/higgs-tts-3-4b).

## Hasznos beállítások (Settings)

- `Audio format` → **MP3** (az exporthoz ffmpeg szükséges)
- `tts_num_step` → 16 hallgatáshoz, 32 végleges exporthoz
- `Merge short lines` (coalesce) → 720 ajánlott; a szegmenshatárokat az illesztett vágás tartja tisztán
- `Split coalesced audio` → Aligned (ajánlott)
- `Alignment ASR model` → whisper-small (gyors) … whisper-large-v3-turbo (legpontosabb)
- `MP3 mode` → VBR (a szegmensek közti csend így szinte semmibe nem kerül)
- `Spoken Pauses` → a mondat-, párbeszéd-, kihagyás-, bekezdés- és fejezetszünet füllel hangolható
- `Studio mastering` → kapcsold be, ha egyenletes, hangoskönyv-szabvány hangerőt szeretnél; hasonlítsd össze füllel

## Fejlesztés

A `main` ág a kiadott állapot, a `dev` a mindig működő, összefésült állapot; minden téma saját `feature/<téma>` ágon készül, és tesztelés után olvad vissza a `dev`-be.

Tesztek futtatása:

```bash
cd reader
.venv/bin/python -m unittest discover -s tests
```

A környezeti változók az `AURIS_STUDIO_` előtagot használják (`AURIS_STUDIO_MPS_DTYPE`, `AURIS_STUDIO_HIGGS_MPS_DTYPE`, `AURIS_STUDIO_OFFLINE`, `AURIS_STUDIO_USE_LOCAL_WHEELS`, `AURIS_STUDIO_WHEELS_DIR`).

## Köszönet

Az eredeti Auris projektért köszönet **nikhilprasanth**-nak, a Higgs-integrációért és a magyar nyelvi támogatásért **mp3pintyo**-nak, a TTS-motorokért pedig a [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) és a [Boson AI](https://huggingface.co/bosonai) csapatának. Az Auris Studio licence az eredeti projektét követi (lásd `LICENSE`).
