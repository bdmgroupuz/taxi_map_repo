# Yo'llar xaritasi

Haydovchi ilovasi yo'l bo'yicha masofani telefonning o'zida, internetsiz hisoblaydi. Buning uchun
O'zbekiston yo'llarining tayyor grafi kerak — bu repozitoriy uni yasaydi va tarqatadi.

- `build_road_graph.py` — OpenStreetMap (.osm.pbf) dan graf faylini yasaydi
- `.github/workflows/build.yml` — har yakshanba o'zi ishga tushib, natijani shu repozitoriyning
  Releases'iga (`map` tegi) joylaydi

Ilova ikkita faylni oladi:

- `releases/download/map/uz_roads.json` — versiya (bir necha bayt, haftada bir tekshiriladi)
- `releases/download/map/uz_roads.bin.gz` — xarita (~22 MB, faqat o'zgargan bo'lsa yuklanadi)

Repozitoriy ochiq bo'lishi shart: telefonlar faylni parolsiz yuklab oladi. Bu yerda faqat xarita
skripti turadi — ilova kodi va kalitlar bu repozitoriyga qo'yilmaydi.

## Qo'lda ishga tushirish

GitHub'da: **Actions → Yo'llar xaritasi → Run workflow**.

Kompyuterda:

```bash
pip install osmium
curl -L -o uzbekistan.osm.pbf https://download.geofabrik.de/asia/uzbekistan-latest.osm.pbf
python build_road_graph.py uzbekistan.osm.pbf uz_roads
```

## Format o'zgarsa

Fayl formati `build_road_graph.py` va ilovadagi `service/routing/RoadGraphIO.kt` da bir xil
bo'lishi shart (`VERSION` / `FORMAT_VERSION`). Formatni o'zgartirsangiz versiyani oshiring —
eski ilovalar yangi faylni rad etib, eski xaritasida ishlayveradi.

Ma'lumot manbai: © OpenStreetMap contributors, ODbL litsenziyasi.
