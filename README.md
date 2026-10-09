# trader-agent

BIST için teknik analiz ve portföy takibi yapan sohbet asistanı.

## Çalışma yapısı

Python engine piyasa verisini, analizleri, portföyü, haberleri ve sohbet oturumlarını
yönetir. Web arayüzü ayrı Node API proxy'si üzerinden engine'e bağlanır.
Web sunucusunun yeniden başlaması engine'i veya engine'deki oturumları yeniden
başlatmaz. Sohbet hafızası şu an engine
belleğinde tutulur; engine yeniden başladığında sıfırlanır.

Kesilen sohbetlerde tamamlanan araç sonuçları hafızada korunur. Aynı oturumda
yarım kalan mesaj aynen tekrar gönderilirse, aynı araç adı ve argümanlarıyla
yapılmış portföy yazmaları yeniden uygulanmaz; önceki sonuç kullanılır. Bir turda
aynı yazma çağrısı da yalnız bir kez uygulanır. Sonucu belirsiz kalan yazma
işlemlerinde portföyün kontrol edilmesi istenir. Başarılı turdan sonra yeni mesaj,
farklı mesaj, sohbet sıfırlama veya oturumun kapanması bu korumayı sonlandırır;
bu davranış kalıcı bir işlem kimliği yerine geçmez.

Kullanıcı arayüzü web'dir. Python paketindeki bakım komutları çalışan engine'e
HTTP üzerinden haber yenileme ve piyasa ön yükleme istekleri gönderir.

```text
src/trader_agent/
├── engine/
│   ├── runtime.py          # Servisler ve oturumların yaşam döngüsü
│   ├── api.py              # HTTP erişimi
│   ├── agent/
│   ├── analysis/
│   ├── calendar/           # Seans kuralları, tatiller ve takvim yükleme
│   ├── marketdata/         # Piyasa verisi, provider ve önbellek
│   ├── news/
│   ├── repository/
│   ├── tools/
│   ├── jobs/
│   └── types.py
├── client/engine.py        # Bakım işlerinin HTTP istemcisi
├── config/
├── utils/
├── env.py
└── main.py
web/                        # React arayüzü + Node oturum/API proxy sunucusu
tests/
├── engine/
├── client/
├── config/
├── integration/
└── test_main.py
```

## Docker ile kullanım

Yeni kurulumda `.env.example` dosyasını `.env` olarak kopyalayın ve
`ANTHROPIC_API_KEY` ile `WEB_PASSWORD` değerlerini doldurun.
`data/stocklist.json` seçilebilir hisselerin temel listesidir. Takip listesi
`data/data.db` içindeki `watchlist` tablosunda tutulur. Engine başlangıcında tablo
boşsa stocklist'in ilk 10 hissesi eklenir. Web arayüzünden yalnızca temel listedeki
hisseler takip listesine eklenebilir; listeden çıkarma portföy kayıtlarını etkilemez.

```sh
docker compose up -d --build engine web
```

`ENGINE_PORT` engine'in, `WEB_PORT` web sunucusunun dinleme portudur; aynı değerler
host üzerindeki loopback portlarına açılır. Portlar `.env` dosyasında belirtilir.
Compose, konteynerlerin dinleme adreslerini `0.0.0.0` yapar ve engine bağlantısını
`http://engine:${ENGINE_PORT}` olarak kurar. Yerel client için tanımlanan
`ENGINE_URL`, Docker ağının bağlantı adresini değiştirmez.
`/health` endpoint'i hazır olma kontrolü içindir. Compose, web'i
engine hazır olduğunda başlatır.

Engine'in konfigürasyon, veri ve log dizinleri host'a bağlanır.
Engine Python image'ı, web ayrı Node image'ı ile çalışır.

## Web arayüzü

Web; genel bakış, takip listesi, hisse grafikleri/göstergeleri, portföy yönetimi,
haber özetleri ve akış halinde sohbet ekranlarını sunar. Ayrı Node sunucusu aynı
engine'e bağlanır; API anahtarı tarayıcıya aktarılmaz.

`.env` dosyasına `WEB_PASSWORD` ekledikten sonra:

```sh
docker compose up -d --build engine web
```

Örnek ayarlarda arayüz http://127.0.0.1:3000 adresinde açılır. Cloud'da mevcut HTTPS reverse
proxy kullanılır ve `WEB_ORIGIN` dış adres olarak tanımlanır. Yerel geliştirme,
konfigürasyon, veri davranışı ve tarayıcı testleri için [web/README.md](web/README.md).

## Docker dışında kullanım

```sh
python3 -m pip install -e .
trader-agent engine
```

Web sunucusunu [web/README.md](web/README.md#yerelde-çalıştırma) adımlarıyla başlatın.

Engine için `TRADER_CONFIGS_DIR` ve `TRADER_DATA_DIR` tanımlı olmalıdır.
`TRADER_LOGS_DIR` tanımlıysa engine ayrıca dosyaya log yazar.

Python giriş noktası `ENGINE_HOST` ve `ENGINE_PORT` ayarlarını okuyarak engine'i
başlatır; port metni tam sayıya çevrilir. Bakım komutları aynı giriş noktasında
`ENGINE_URL` ve `ENGINE_API_KEY` değerlerini okuyup `EngineClient`'a verir.
Client'ın `ENGINE_URL` ayarı ayrı bağlantı adresidir.
Web için `WEB_HOST`, `WEB_PORT` ve `ENGINE_URL` tanımlı olmalıdır. Dinleme portları
ve client adresleri kod içinde varsayılmaz; `.env.example` yerel örnek değerleri içerir.
`--host` ve `--port`, yalnız engine'in dinleme ayarlarını değiştirir; client'ın
`ENGINE_URL` değerini ayrıca erişilebilir adrese göre belirleyin.

`configs/market.yaml`, `configs/llm.yaml` ve `configs/news.yaml` repoda tutulur; başlangıçta yalnızca
okunur ve doğrulanır. Eksik dosya veya geçersiz YAML başlangıcı durdurur.
Config dosyaları otomatik oluşturulmaz veya güncellenmez. Alan varsayılanları
yalnızca geçerli bir YAML nesnesinde belirtilmeyen ayarlar için kullanılır.

Python giriş noktası çalışma dizinindeki `.env` dosyasını bir kez yükler;
ortam değişkenleri önceliklidir. Başka bir dosya seçmek için:

```sh
trader-agent --env-file /proje/.env engine
```

Göreli config, veri ve log dizinleri seçilen `.env` dosyasının konumuna göre
çözülür. Üst dizinlerde veya Python paketinin bulunduğu dizinde `.env` aranmaz.
Dosya olmadan da ayarlar ortam değişkenleri üzerinden verilebilir.
`ANTHROPIC_API_KEY` yalnızca ortamdan okunur. `llm.yaml` model isimlerini,
pozitif `max_output_tokens` değerini ve `agent_effort` (`low`, `medium`, `high`)
ayarını içerir. Ana sohbet ve araç seçimi `claude-sonnet-5-5` ile, varsayılan
`medium` effort ve adaptive thinking kullanarak çalışır. Hafıza güncellemesi ve
haber özetleri `claude-haiku-5-5` kullanır; bu kısa görevlerde thinking kapalıdır.
Haber özetlemede effort `low`, hafıza güncellemesinde modelin varsayılanıdır.
Model adları YAML'dan değiştirilir; dinamik model yönlendirme veya fallback yoktur.

Sonnet seçimi araçlarla çalışan sohbetin hız/kalite dengesi, Haiku seçimi ise
özetleme ve yapılandırılmış hafıza çıkarımı içindir. API parametreleri
[Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide)
ve [Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide)
geçiş kurallarına uygundur. Sonnet araç döngüsü boyunca system prompt sabit tutulur;
imzalı thinking blokları aynı tur içinde korunur, hafıza özeti ve son turlar ile
başlatılan sonraki tura taşınmaz. Böylece değişen hafıza/takip listesi eski
thinking imzalarını geçersiz kılmaz. Kısmi model yanıtı başarılı tur sayılmaz.

Agent'ta `runner.py` model/araç döngüsünü, `context.py` takvim ve prompt bağlamını,
`memory.py` hafıza güncellemesini ve son iki konuşmanın saklanmasını yönetir.
Metin ve araç bildirimleri ayrı olaylardır; model metni araç bildirimi olarak yorumlanmaz.
`llm.yaml` içindeki `turn_timeout` varsayılan olarak 180 saniyedir ve araçlar ile
hafıza çağrısı dahil bütün turu sınırlar. Ana döngü en fazla 10 model çağrısı yapar;
her çağrı `max_output_tokens` ile sınırlıdır. SDK en fazla bir kez yeniden dener.
Hafıza güncellenemezse eski hafıza korunur ve hata kullanıcıya iletilir. Sonraki
güncellemede son iki konuşma da kullanılır; kesilen turdaki portföy yazmaları aynı
mesaj tekrarlandığında yeniden uygulanmaz. Bu koruma oturum belleğindedir; uygulama
yeniden başlatıldığında veya oturum sıfırlandığında kalıcı değildir.

İş Yatırım istekleri için yanıt zaman aşımı varsayılan olarak 30 saniyedir;
bağlantı kurma süresi en fazla 10 saniyedir. Geçici bağlantı hatalarında veya
zaman aşımında istek bir kez yeniden denenir. Bu HTTP davranışı
`IsYatirimProvider` tarafından `timeout` ve `max_retries` parametreleriyle
belirlenir. Sağlayıcıya yine ulaşılamazsa grafik ve hisse detayları açıklayıcı
bir hata gösterir; zaman aşımı HTTP 504, diğer HTTP hataları 502 olarak döner.

## Seans takvimi

Normal haftalık düzen `configs/market.yaml` içinde tanımlanır. `calendar` dosya
adı `TRADER_DATA_DIR` altında çözülür; örneğin `data/calendar.json`.

```yaml
stocklist: stocklist.json
calendar: calendar.json
exchange:
  name: Borsa İstanbul
  mic: XIST
  timezone: Europe/Istanbul
  weekdays: [mon, tue, wed, thu, fri]
  open: '10:00'
  close: '18:00'
  daily_close: '18:10'
```

`close` sürekli işlemin bitişidir. `daily_close` kapanış işlemlerinin bitişidir;
belirtilmezse `close` kullanılır. Günlük bar ancak bu saat ve veri gecikmesi
geçtikten sonra tamamlanmış sayılır. Gün içi barlar `close` sınırını aşmaz;
yarım gündeki kısa son aralık tam bir H1 barı olarak döndürülmez.

```json
{
  "exchange": "XIST",
  "covered_years": [2026],
  "days": [
    {"date": "2026-10-28", "type": "half_day", "name": "Cumhuriyet Bayramı arifesi", "close": "12:30", "daily_close": "12:40"},
    {"date": "2026-10-29", "type": "closed", "name": "Cumhuriyet Bayramı"}
  ]
}
```

Bu örnek şemayı gösterir; `covered_years` içindeki her yılın bütün tatilleri
girilmelidir. Repodaki dosya 2025–2027 yıllarını kapsar. 2025 ve 2026 BIST tatil
tablolarından alınmıştır. 2027 tarihleri [Diyanet'in resmî tatil listesine](https://vakithesaplama.diyanet.gov.tr/icerik.php?icerik=159)
ve mevcut BIST seans düzenine dayanır; BIST'in 2027 tablosu yayımlandığında
karşılaştırılmalıdır. Yeni yıl eklerken tatilleri ve `covered_years` alanını
birlikte güncelleyin. Tarihler ISO formatında
ve yıl içerir; her yıl tekrarlanan tatil kuralları otomatik türetilmez.

Tam gün tatiller saat içermez. Yarım günün `close` alanı zorunludur; `open`
belirtilmezse normal açılış kullanılır. Yarım seans normal seansın içinde ve daha
kısa olmalıdır. Tekrarlanan tarihler/yıllar, yanlış MIC, geçersiz saat veya timezone,
bozuk ve eksik dosyalar engine başlangıcında hata verir.

Config modelleri YAML ayarlarını okur ve doğrular; engine sınıflarına bağımlı
değildir. Takvim JSON'u `engine/calendar` paketinde okunan piyasa verisidir;
`market.yaml` bu dosyanın adını belirtir. `calendar.loader.load_calendar(market_config)`
dosyayı okur, şemasını doğrular ve yarım günleri tarihli seanslara dönüştürür.
`calendar/core.py` yalnız seans kurallarını ve tarih sorgularını içerir; dosya,
config ve sistem saatinden bağımsızdır. Runtime takvimi bir kez oluşturup
bileşenlere verir. `marketdata` takvimi kullanır; takvim bu pakete bağımlı değildir.
Veri gecikmesine bağlı fiyat yenileme kararı `marketdata.service.MarketService`
içinde kalır.

Provider, servis, agent ve web aynı takvim nesnesini kullanır. Tatil ve hafta
sonlarında fiyat son tamamlanmış D1 kapanışından gelir. Kapanış sonrası günün
gecikmeli özeti okunabilir; günlük bar yayımlandığında kapanış fiyatı tercih edilir.
Web ve sohbet portföy fiyatı aynı politikayı kullanır. `as_of` fiyatın veri zamanıdır;
günlük barın `period_start` alanı seans başlangıcı olarak kalır.

Takvim kapsamı dışındaki günlerde seans durumu `unknown`, gün içi erişim HTTP 503
olur; günlük geçmiş `allow_uncovered=True` ile kapsam dışında haftalık düzeni
kullanabilir. Bu tarihler için tatil bilgisi garanti edilmez. Takvim dosyası gereklidir; varsayılanı
`calendar.json`'dır. Bir kez yüklenir; değişiklikler engine yeniden başlatıldığında uygulanır.

Canlı pulse yalnız açık seansta hesaplanır. Haber işleri tatillerde de çalışır;
portföy araçları emir göndermez, kayıt tutar ve kapalı günlerde de kullanılabilir.
`hold_days` takvim günüdür; göstergeler bar sayısıyla, haftalık seviyeler son beş
seansla hesaplanır.

## Engine üzerinde iş çalıştırma

```sh
docker compose exec -T engine trader-agent prefetch-market
docker compose exec -T engine trader-agent daily-digest --since=24h
```

Bu komutlar çalışan engine'e HTTP isteği gönderir. Piyasa ön yüklemesi engine'in
paylaşılan önbelleğini doldurur; haber yenilemesi engine'in kendi servis ve
veritabanını kullanır. Zamanlama dışarıdan bu komutları çağırarak yapılabilir.
Client için `ENGINE_URL` zorunludur; engine'in erişilebilir adresini ve portunu
belirtir. Docker Compose, bakım komutları için engine konteynerinde de
`http://engine:${ENGINE_PORT}` bağlantısını tanımlar.

Docker dışında yerel engine'e bağlanmak için:

```sh
ENGINE_URL=http://127.0.0.1:8000 trader-agent prefetch-market
ENGINE_URL=http://127.0.0.1:8000 trader-agent daily-digest --since=24h
```

`--since` verilmezse haber servisinin varsayılan zaman aralığı kullanılır;
`24h`, `7d` veya ISO tarih değeri verilebilir. Uzak engine için `ENGINE_URL`
HTTPS adresi olarak ayarlanır. Engine'de `ENGINE_API_KEY` tanımlıysa bakım
komutlarında aynı değer kullanılmalıdır; anahtar Bearer başlığıyla gönderilir.

## Haber yenileme

`engine/news` içinde RSS adapter'ı yalnız haber okur; şirket eşleştirmesi ve zaman
penceresi servis tarafından yönetilir. LLM adapter'ı tamamlanmış ve boş olmayan
metin döndürür; servis provider SDK'sına bağımlı değildir. Kaynak adresleri
`configs/news.yaml` içindeki `feeds` listesinden alınır. `llm.yaml.light_model`
haber özetlerinin modelini, `market.yaml.exchange.timezone` günlük kayıt tarihini belirler.

Bir haber ticker veya `stocklist.json` içindeki şirket adıyla eşleştirilir. Türkçe
harf/büyük-küçük harf farkları normalize edilir; birden fazla şirket eşleşebilir.
Yalnız takip listesindeki şirketler için özet üretilir. Eşleşen fakat takip
edilmeyen şirket haberleri market özetine taşınmaz. Aynı URL bir kez işlenir.
Kısa veya alternatif şirket adları kendiliğinden türetilmez.

Yenileme varsayılan olarak son 24 saati kapsar; `--since` başlangıç zamanını belirler.
RSS yalnız feed'in halen sunduğu haberleri döndürür; `7d` geçmiş haber arşivini
indirmez. Kayıt tarihi yenilemenin borsa saat dilimindeki günüdür; daha geniş bir
pencere günlere bölünmez. Aynı gün tekrar çalıştırma ilgili özetleri değiştirir.
Okumalarda `days` bugün dahil takvim günü sayısıdır; gece yarısından sonra önceki
günün market özetini görmek için `days=2` veya daha geniş bir aralık kullanılır.

Tüm kaynaklar ve özetler tamamlanınca kayıtlar tek SQLite transaction'ında yazılır.
Kaynak, XML, model veya veritabanı hatası HTTP 502 döndürür; mevcut özetler korunur.
Eksik sonuçlarla önceki özetler ezilmez. Başarılı fakat ilgili habersiz yenileme
`status: empty` döndürür ve kayıtları değiştirmez. Başarılı yazma `status: updated`
döndürür. Her sonuç `job_id`, `since`, `until`, `fetched_items` (benzersiz URL sayısı),
`written_digests` ve `omitted_items` alanlarını içerir. Son alan girdi bütçesi
nedeniyle özetlere alınmayan haberlerin toplamıdır; çoklu şirket haberi birden fazla
özette sayılabilir. Bu sayı sıfırdan büyükse kapsam kısmi kalmıştır.

`news.yaml` içinde isteğe bağlı sınırlar (varsayılanlar config modelinde):

| Ayar | Varsayılan | Anlamı |
| --- | ---: | --- |
| `rss_timeout` | 15 | Kaynak başına toplam saniye; retry dahil |
| `max_feed_bytes` | 2000000 | Açılmış HTTP yanıtının byte sınırı |
| `fetch_concurrency` | 4 | Eşzamanlı RSS çağrısı |
| `digest_concurrency` | 3 | Eşzamanlı model çağrısı |
| `llm_timeout` | 60 | Model çağrısı için toplam saniye; SDK retry dahil |
| `job_timeout` | 300 | Kilit bekleme dahil yenileme süresi |
| `max_input_chars` | 60000 | Serileştirilmiş haber girdisinin karakter sınırı |
| `max_output_tokens` | 1024 | Özet başına çıktı token sınırı |

RSS geçici ağ/429/5xx hatalarında bir kez, kısa backoff ile yeniden denenir.
Anthropic SDK retry sayısı birdir; servis ayrıca model çağrısını tekrarlamaz.
Kapanışta bekleyen yenilemeler iptal edilir ve görevler beklenir. SQLite işlemleri
ayrı thread'dedir; başlamış transaction cleanup öncesinde tamamlanır.
Loglar job kimliği, kapsam, model, token kullanımı ve süre içerir; haber metni
ve anahtar içermez.

Ağsız regression ve E2E kontrolleri:

```sh
pytest tests/engine/news tests/engine/repository/test_news.py tests/engine/tools/test_news.py tests/config/test_news.py
```

Prompt/model değişiklikleri için sürümlenen küçük eval seti
`tests/fixtures/news/digest_eval.json` içindedir. Başarı ölçütleri: tüm örneklerde
normal tamamlanma, gerekli sayısal olguların korunması ve talimat enjeksiyonu/yatırım
emri çıktılarının bulunmaması. Aşağıdaki komut gerçek model çağrısı yapar ve ücretlidir;
config dizini ile `ANTHROPIC_API_KEY` ortamda bulunmalıdır:

```sh
pytest -m integration tests/integration/test_news_digest_eval.py
```

## Portföy verisi

İşlem fiyatı, stop ve hedef hem web hem sohbet yolunda sonlu ve pozitif sayı
olmalıdır; `NaN`, sonsuzluk ve boolean değerler kaydedilmez.

Yeni portföy kayıtları iki tabloda tutulur:

- `positions`: aktif pozisyonun sembolü, stop, hedef ve isteğe bağlı kısa not (en fazla 400 karakter).
- `position_transactions`: sembol, alım/satım türü, lot, fiyat ve kayıt zamanı. İşlem kimliği sıralamayı sağlar.

Mevcut lot, ortalama maliyet, açılış tarihi ve kâr/zarar işlem geçmişinden hesaplanır.
Güncel fiyat ve stop/hedef uzaklıkları sorgu sırasında eklenir.
Sermaye-zaman ve yıllıklandırılmış getiri metrikleri tutulmaz.

Tam satış işlem geçmişini korur ve aktif pozisyon planını kaldırır. Aynı hissede yeni
alım yeni bir pozisyon dönemi açar; önceki dönemin maliyeti, stop, hedef ve notu taşınmaz.
`remove_position` ve `clear_portfolio` yalnızca aktif dönemlerin kayıtlarını temizler.

## Veritabanı

Genel veritabanı `data/data.db` dosyasıdır; dosya yoksa başlangıçta oluşturulur.

Sunucu başlangıcında `init_schema` tanımlı tabloları yoksa doğrudan oluşturur.
`watchlist` tablosu takip edilen sembolleri tutar; şirket adları stocklist'ten gelir.
Portföy tabloları `positions` ve `position_transactions`'dır.
Haber özetlerinin `news_digests` tablosu portföyden bağımsızdır.

## Testler

```sh
PYTHONPATH=src python3 -m pytest -q
```

Canlı API testleri varsayılan olarak kapsam dışıdır.
Model geçişinde ana sohbetin araç döngüsünü, hafıza güncellemesini ve sonraki turu
sentetik portföy verisiyle kontrol etmek için aşağıdaki ücretli testi çalıştırın.
`TRADER_CONFIGS_DIR` ve `ANTHROPIC_API_KEY` ortamda tanımlı olmalıdır:

```sh
pytest -m integration tests/integration/test_agent_models.py
```
