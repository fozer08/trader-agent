# Trader Agent Web

Türkçe, tek kullanıcılı BIST çalışma alanı. React + TypeScript + Vite arayüzü,
ayrı Node HTTP sunucusu üzerinden mevcut engine'e bağlanır. Node sunucusu yalnızca
statik dosyaları, giriş oturumunu ve API proxy'sini yönetir; piyasa verisi,
analiz, portföy ve agent engine'de kalır.

## Ekranlar

- Genel bakış: portföy toplamları, D1 trend dağılımı, fiyat grafiği, haber özetleri ve risk radarı.
- Takip listesi: temel hisse listesinden ekleme, takipten çıkarma, hisse/şirket
  araması, trend filtresi, sembol/değişim/RSI sıralaması.
- Hisse detayları: günlük mum, 5/15 dakikalık fiyat grafiği, göstergeler ve referans seviyeleri.
- Portföy: alım/satım kaydı, stop/hedef/not düzenleme, işlem geçmişi ve kapanmış pozisyonlar.
- Asistan: markdown yanıtlar, NDJSON akışı, araç etkinliği, yeni sohbet ve akışı durdurma.

## Yerelde çalıştırma

Node.js 22.12+ ve engine'in Python bağımlılıkları gerekir. Proje kökünde engine'i başlatın:

```sh
PYTHONPATH=src python3 -m trader_agent engine
```

Başka bir terminalde:

```sh
cd web
npm ci
npm run build
npm start
```

Örnek `.env` değerlerinde arayüz http://127.0.0.1:3000 adresindedir. `npm start`
kökteki `.env` dosyasını okur; ortam değişkenleri önceliklidir. `WEB_HOST`,
`WEB_PORT` ve `ENGINE_URL` zorunludur. Yerel `.env.example`, engine'e
`http://127.0.0.1:8000` üzerinden bağlanır; portu değiştirirseniz URL'yi de güncelleyin.
Başka bir dosya kullanmak için `node --env-file=/proje/.env server.mjs` çalıştırın.

Geliştirme için Node API sunucusu açıkken üçüncü terminalde `cd web && npm run dev`
çalıştırın. Vite http://127.0.0.1:5173 üzerinde açılır ve `/api` isteklerini
`.env` veya ortam değişkenlerinde belirtilen web adresine ve portuna gönderir.

## Docker

Kökteki `.env` dosyasına `WEB_PASSWORD` ekleyin:

```dotenv
WEB_PASSWORD=uzun-bir-kisisel-parola
WEB_PORT=3000
```

Ardından proje kökünde:

```sh
docker compose up -d --build engine web
```

Web portu host üzerinde yalnızca loopback'e bağlanır. Web konteyneri root olmayan `node`
kullanıcısıyla çalışır. Dış ağda dinleyen Node sunucusu `WEB_PASSWORD` olmadan
başlatılmaz; yalnızca yerel loopback kullanımında parola isteğe bağlıdır.
Compose, `ENGINE_URL` değerini Docker ağındaki engine servisi ve `ENGINE_PORT`
ile kurar. `ENGINE_PORT` ve `WEB_PORT`, servislerin hem içeride hem host üzerinde
kullandığı portlardır; sağlık kontrolleri de aynı değerleri kullanır.

## Cloud erişimi

Mevcut HTTPS reverse proxy'nizi host'taki 127.0.0.1:3000'e yönlendirin.
`WEB_ORIGIN=https://trader.example.com` ve `WEB_PASSWORD` tanımlayın.
Engine portunu dışarı açmak gerekmez. `ENGINE_API_KEY` sadece Node'dan engine'e
giden istekte kullanılır; frontend bundle'ına veya tarayıcıya aktarılmaz.

Nginx location örneği:

```nginx
location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_buffering off;
    proxy_read_timeout 600s;
}
```

Giriş oturumu 12 saat geçerlidir; çerez HttpOnly/SameSite=Strict'tir ve HTTPS
origin tanımlandığında Secure olur. Çıkış oturumu iptal eder; web sunucusunun
yeniden başlaması giriş oturumlarını sıfırlar. API yazma istekleri origin kontrolünden
geçer. Başarısız girişler IP başına 15 dakikada 10 denemeyle sınırlıdır.
Debug ve bakım işlerini çalıştırma erişimi web proxy'sinden sunulmaz.

## Veri davranışı

- Seçilebilir hisseler `data/stocklist.json` dosyasından gelir; takip edilenler
  `data/data.db` içindeki `watchlist` tablosunda saklanır. Boş tablo engine
  başlangıcında dosyadaki ilk 10 hisseyle doldurulur. UI'daki değişiklikler
  asistanın takip listesine, taramaya ve bakım işlerine yeniden başlatma olmadan yansır.
- Takip listesi ilk istekte sembolleri hemen döndürür ve arka planda kademeli dolar.
  Beş dakikalık yenileme aralığı ve engine'in ortak önbelleği kullanılır. Tarih,
  açılış, erken/normal kapanış ve günlük barın tamamlanma sınırlarında cache yenilenir.
  Soğuk önbellekte bütün listenin hazırlanması birkaç dakika sürebilir.
- Sağlayıcının 15 dakikalık gecikmesi ve her fiyatın veri zamanı görünürdür.
  Seans verisi alınamazsa son tamamlanan günlük kapanış açıkça etiketlenir.
- Gün içi kaynak fiyat örnekleridir; gerçek OHLC/hacim içermez. Bu nedenle gün
  içinde çizgi grafiği, günlük veride mum grafiği kullanılır.
- Fiyatı alınamayan pozisyonlarda kâr/zarar ve portföy toplamı boş bırakılır.
- Haberler kayıtlı günlük özetlerden okunur. Sayfa açılışı LLM çağrısı veya haber
  yenileme işi çalıştırmaz. Asistan yalnızca mesaj gönderildiğinde çağrılır.
- Seans etiketi engine'in ortak takviminden gelir. Tatil nedeni, yarım günün gerçek
  saatleri ve sonraki seans gösterilir. Takvim kapsamı dışında açık seans varsayılmaz.
  Ayrıntılar için kökteki [seans takvimi açıklaması](../README.md#seans-takvimi).
- Fiyat zamanı ve grafik saatleri metadata'nın timezone alanıyla biçimlendirilir;
  yalnız tarih içeren haber/takvim günleri başka bir güne kaydırılmaz.
- Asistan hafızası engine belleğindedir; kalıcı konuşma arşivi yoktur. Web sohbet
  oturumları birbirinden bağımsızdır ve aynı portföyü kullanır.
- İşlem kayıt zamanı kaydedildiği andır. Web arayüzü geçmiş tarihli işlem girişi sunmaz.

## Kontroller

```sh
# Proje kökünde
PYTHONPATH=src python3 -m pytest -q

# web içinde
npm run build
npm test
npx playwright install chromium
npm run test:e2e
```

Tarayıcı testleri masaüstü ve mobilde giriş, arama, hisse inceleme, grafik aralığı,
sohbet, alım, plan düzenleme, işlem geçmişi, başarısız satış, tatil, yarım gün,
takvim kapsamı ve timezone davranışlarını
test verileriyle doğrular. API testleri gerçek geçici SQLite veritabanında
kısmi/tam satışları ve işlem geçmişinin korunmasını kontrol eder.

Playwright'ın indirdiği Chromium mevcut işletim sistemini desteklemiyorsa
kurulu Chrome kullanılabilir:

```sh
TRADER_BROWSER_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:e2e
```

Grafikler TradingView Lightweight Charts™ kullanır. Telif bildirimi `/NOTICE.txt`
adresinde ve grafik bağlantısında bulunur.
