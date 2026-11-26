# KSB utakmice

Besplatna web aplikacija za pretragu utakmica sa ksb.rs po ekipi i datumu.
Klik na utakmicu otvara njenu stranicu na ksb.rs.

## Kako radi
- `scraper/scrape.py` preuzima utakmice sa ksb.rs i upisuje ih u `utakmice.json`
- GitHub Actions pokreće skriptu na svakih sat vremena (`.github/workflows/osvezi.yml`)
- `index.html` je aplikacija: čita `utakmice.json` i pretražuje ga
- GitHub Pages besplatno hostuje stranicu

## Postavljanje (jednom)
1. Na github.com napravi **novi repozitorijum** (npr. `ksb-utakmice`), podesi **Public**.
2. Raspakuj ZIP i ubaci **sve** fajlove u repozitorijum (Add file → Upload files).
   Važno: mora da se prebaci i skriveni folder `.github`
   (na Mac-u: Cmd+Shift+. da se prikažu skriveni folderi).
3. **Settings → Actions → General → Workflow permissions** → izaberi
   **Read and write permissions** → Save.
4. **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main`, folder `/ (root)` → Save.
5. Kartica **Actions** → *Osvezi utakmice* → **Run workflow** (prvo ručno osvežavanje).
   Proveri da je zeleno. Ako je crveno, otvori log i pošalji mi ga.
6. Adresa aplikacije: `https://TVOJE-KORISNICKO-IME.github.io/ksb-utakmice/`

## Na telefonu
- **Android (Chrome):** meni ⋮ → *Dodaj na početni ekran* / *Instaliraj aplikaciju*
- **iPhone (Safari):** dugme za deljenje → *Dodaj na početni ekran*

## Dodavanje stranice sa utakmicama za doigravanje
Otvori `scraper/scrape.py` i u listu `SOURCES` dodaj adresu stranice, npr.
`"https://www.ksb.rs/takmicenje1209/"`. Skripta traži linkove oblika `/utakmicaBROJ/`.
Ako stranica ima drugačiji izgled, javi mi pa prilagodim.

## Ako osvežavanje stane
GitHub ponekad isključi zakazane zadatke posle dužeg mirovanja repozitorijuma.
Tada u kartici Actions samo ponovo klikni **Run workflow**.
