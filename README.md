<h1>Vodafone TV</h1>
<h3>Kodi doplněk pro Vodafone TV</h3>

<p>Podporuje: Živé vysílání, archiv (catchup i přehrávání od začátku pořadu), nahrávky,
vyhledávání, správa kanálů a přepínání profilů domácnosti. Dále export m3u/epg do IPTV Simple Client</p>

<h4>Co je potřeba</h4>
<ul>
  <li>platné předplatné Vodafone TV,</li>
  <li>soubor <code>.wvd</code> s Widevine CDM ve složce <code>addon_data</code>
      doplňku (nebo cesta k němu v nastavení). Pokud ho nemáte, addon se pokusí získat nový unikátní <code>.wvd</code> soubor ze serveru.</li>
</ul>

<h4>Přihlášení</h4>
<p>Doplněk se ptá na přihlášení jen tehdy, když zařízení ještě není
registrované, a nabídne dvě možnosti:</p>
<ul>
  <li><b>QR kód</b> -- stejně jako aplikace na TV: doplněk zobrazí QR kód
      a PIN a čeká, až kód naskenujete v mobilní aplikaci Vodafone TV
      (Nastavení / Účet / Přihlášení k jinému zařízení),</li>
  <li>jméno a heslo,</li>
</ul>
<p>Ručně lze přihlášení spustit v nastavení tlačítkem
<i>Přihlásit zařízení</i>.</p>

<p>Doplněk si licenční požadavek sestavuje sám, protože InputStream Adaptive
neumí zapnout privacy mode, který licenční server vyžaduje
(<a href="https://github.com/xbmc/inputstream.adaptive/issues/1850">ISA #1850</a>).
Získané klíče pak předá přehrávači jako ClearKey.</p>

<p>Device ID se vygeneruje při prvním spuštění a uloží do <code>addon_data</code>.
</p>

<hr>

<h2>Časté problémy (FAQ)</h2>

<p>Hlášky níže jsou to, co doplněk ukáže v notifikaci. Podrobnosti k nim
vždycky najdete v logu Kodi (viz <i>Kde najdu log</i> na konci).</p>

<h4>„Vaše domácnost už využívá všechna povolená zařízení – uvolněte jedno z nich"</h4>
<p>Server se zařízeními (wvd-vault) půjčuje omezený počet <code>.wvd</code> na
jednu domácnost (ve výchozím stavu <b>tři</b>). Vaše domácnost už je vyčerpala.
<b>Čekáním se to nespraví</b> -- na rozdíl od hlášky o volných zařízeních níže.
Máte dvě možnosti:</p>
<ul>
  <li>požádat správce serveru, ať uvolní zařízení, které už nepoužíváte
      (typicky po reinstalaci doplňku, kdy vznikne nové Device ID a to staré
      dál drží svůj soubor),</li>
  <li><b>obstarat si vlastní <code>.wvd</code></b> -- pak server vůbec
      nepotřebujete a žádný limit se vás netýká. Jak na to, viz další bod.</li>
</ul>

<h4>Jak si obstarám vlastní <code>.wvd</code>?</h4>
<p>Soubor <code>.wvd</code> je Widevine L3 CDM vytažené z <b>rootovaného
zařízení s Androidem</b> (běžný telefon nebo tablet) -- k tomu slouží veřejně
dostupné nástroje typu <i>KeyDive</i>. Hotový soubor pak stačí zkopírovat do
složky <code>addon_data</code> doplňku nebo na něj ukázat v
<i>Nastavení → DRM → Soubor .wvd</i>.</p>
<ul>
  <li>Musí jít o CDM, které licenční server Nagra akceptuje. <b>Běžný
      Android telefon/tablet projde</b>; keyboxy z některých netypických
      systémů (např. LineageOS na Raspberry Pi) licenční server odmítá -- v
      logu se to projeví jako <code>LSException 4044</code> a žádným
      nastavením to nespravíte.</li>
  <li><b>Nesdílejte svůj <code>.wvd</code> s ostatními.</b> Jedno CDM používané
      na více místech si server dřív nebo později spojí a může ho zablokovat.</li>
</ul>

<h4>„Není volné zařízení. Zkuste to prosím za pár dní."</h4>
<p>Server se zařízeními právě nemá žádné volné <code>.wvd</code>, které by
půjčil. Tohle skutečně stačí zkusit později -- nebo si obstarat vlastní
<code>.wvd</code> (viz výše).</p>

<h4>„Server se zařízeními neověřil vaše předplatné – zkuste vytvořit novou session"</h4>
<p>Než server půjčí <code>.wvd</code>, ověří si, že opravdu máte živé
předplatné Vodafone TV: doplněk mu podepíše kontrolní dotaz svým přihlašovacím
klíčem a server si odpověď ověří přímo u Vodafonu. Když tohle neprojde,
bývá to zastaralá session. Zkuste <i>Přihlášení → Nová session</i>, případně
znovu přihlásit zařízení.</p>

<h4>„Zařízení lze vyžádat až po přihlášení k Vodafone TV"</h4>
<p>Půjčení <code>.wvd</code> vyžaduje přihlášené zařízení. Nejdřív se
přihlaste (<i>Přihlášení → Přihlásit zařízení</i>), teprve pak zkuste
přehrávání znovu.</p>

<h4>„Server se zařízeními neodpovídá" / „odmítl klíč API"</h4>
<p>Špatná adresa nebo klíč serveru v nastavení, případně server neběží.
Zkontrolujte obojí v <i>Nastavení → DRM</i>. Pokud server běží na vlastním
certifikátu (self-signed), zapněte navíc volbu neověřovat certifikát --
jinak se k němu doplněk odmítne připojit, protože při ověřování posílá váš
přihlašovací klíč.</p>

<h4>„Chybí soubor .wvd – zkontrolujte nastavení DRM"</h4>
<p>Doplněk nenašel žádné CDM ani nemá nastavený server. Zkopírujte
<code>.wvd</code> do <code>addon_data</code>, nastavte na něj cestu v
<i>Nastavení → DRM</i>, nebo si nechte půjčit zařízení ze serveru.</p>

<h4>„Kanál se nepodařilo odemknout – pravděpodobně není součástí vašeho předplatného"</h4>
<p>Kanál nemáte předplacený. Brána ho hlásí jako vnitřní chybu, takže si tím
doplněk nemůže být úplně jistý, ale v praxi je to skoro vždy tenhle důvod.
Nepředplacené kanály můžete ze seznamu skrýt v nastavení.</p>

<h4>„Přehrávání blokuje jiná relace. Zkuste to prosím za chvíli znovu"</h4>
<p>Vodafone povoluje jen jedno přehrávání najednou. Zavřete přehrávání
jinde (mobilní aplikace, web, TV) a chvíli počkejte -- server relaci uvolní
sám, ale ne okamžitě.</p>

<h4>„Licenční server odmítl požadavek"</h4>
<p>Nejčastěji <code>.wvd</code>, které licenční server nebere (viz
<i>Jak si obstarám vlastní .wvd</i>), nebo CDM, které už bylo zablokované.
Zkuste jiný soubor <code>.wvd</code>.</p>

<h4>Živé vysílání na chvíli zamrzne, hlavně po přepnutí kanálu</h4>
<p>Nejnovější segment živého streamu nemusí být na CDN ještě hotový; přehrávač
si na něj počká a stream na ~10 s zamrzne. Řeší to volba <i>Zpoždění za živým
vysíláním</i> (<i>Nastavení</i>, výchozí 5 s): čím vyšší, tím stabilnější, ale
tím dál od reálného času. Archivu a nahrávek se to netýká.</p>

<h4>QR kód nejde naskenovat</h4>
<p>Skener v mobilní aplikaci Vodafone TV čte jen <b>invertované</b> QR kódy
(světlé na tmavém) -- doplněk je proto tak rovnou generuje. Skenujte v aplikaci
v <i>Nastavení / Účet / Přihlášení k jinému zařízení</i>. Když to nejde ani
tak, přihlaste se jménem a heslem; výsledek je stejný.</p>

<h4>Kde najdu log</h4>
<p>V <i>Nastavení → Logování</i> zapněte logování požadavků a odpovědí, chybu
zopakujte a pošlete log Kodi (<code>kodi.log</code>). Doplněk do něj píše
řádky začínající <code>Vodafone TV</code>.</p>

<hr>

<h1>Vodafone TV (English)</h1>
<h3>Kodi addon for Vodafone TV</h3>

<p>Supports: live TV, archive (catch-up as well as playback from the start of a
running programme), recordings, search, channel management and switching between
household profiles. It can also export m3u/EPG to IPTV Simple Client.</p>

<h4>What you need</h4>
<ul>
  <li>a valid Vodafone TV subscription,</li>
  <li>a <code>.wvd</code> file with a Widevine CDM in the addon's
      <code>addon_data</code> folder (or a path to it in the settings). If you
      have none, the addon tries to lease a unique <code>.wvd</code> from a
      server.</li>
</ul>

<h4>Signing in</h4>
<p>The addon only asks you to sign in while the device is not registered yet,
and offers two ways:</p>
<ul>
  <li><b>QR code</b> -- the same way the TV app does it: the addon shows a QR
      code and a PIN and waits for you to scan the code in the Vodafone TV
      mobile app (Settings / Account / Sign in on another device),</li>
  <li>username and password.</li>
</ul>
<p>You can start it by hand from the settings with the <i>Sign in device</i>
button.</p>

<p>The addon builds the license request itself, because InputStream Adaptive
cannot turn on the privacy mode the license server insists on
(<a href="https://github.com/xbmc/inputstream.adaptive/issues/1850">ISA #1850</a>).
The keys it gets back are handed to the player as ClearKey.</p>

<p>The Device ID is generated on first run and stored in
<code>addon_data</code>.</p>

<h2>FAQ</h2>

<p>The messages below are what the addon puts in a notification. The details
behind them are always in the Kodi log (see <i>Where is the log</i> at the
end).</p>

<h4>"Your household already uses every device allowed -- free one of them"</h4>
<p>The device server (wvd-vault) lends a limited number of <code>.wvd</code>
files per household (<b>three</b> by default), and yours has used them all.
<b>Waiting will not help</b> -- unlike the "no free device" message below. You
have two options:</p>
<ul>
  <li>ask the server's operator to free a device you no longer use (typically
      left over from reinstalling the addon, which generates a new Device ID
      while the old one keeps holding its file),</li>
  <li><b>get your own <code>.wvd</code></b> -- then you do not need the server
      at all and no limit applies to you. See the next entry.</li>
</ul>

<h4>How do I get my own <code>.wvd</code>?</h4>
<p>A <code>.wvd</code> file is a Widevine L3 CDM extracted from a <b>rooted
Android device</b> (an ordinary phone or tablet); publicly available tools such
as <i>KeyDive</i> do that. Copy the resulting file into the addon's
<code>addon_data</code> folder, or point at it in
<i>Settings → DRM → .wvd file</i>.</p>
<ul>
  <li>It has to be a CDM the Nagra license server accepts. <b>An ordinary
      Android phone or tablet works</b>; keyboxes from some unusual systems
      (LineageOS on a Raspberry Pi, for instance) are refused by the license
      server -- it shows up in the log as <code>LSException 4044</code> and no
      setting can fix it.</li>
  <li><b>Do not share your <code>.wvd</code> with anyone.</b> One CDM used in
      several places will sooner or later be correlated and may be blocked.</li>
</ul>

<h4>"No free device. Please try again in a few days."</h4>
<p>The device server has no free <code>.wvd</code> to lend right now. This one
really is worth retrying later -- or get your own <code>.wvd</code> (above).</p>

<h4>"The device server could not verify your subscription -- try creating a new session"</h4>
<p>Before it lends a <code>.wvd</code>, the server checks that you really do
hold a live Vodafone TV subscription: the addon signs a check request with its
login key and the server verifies the answer with Vodafone directly. When that
fails it is usually a stale session. Try <i>Sign in → New session</i>, or sign
the device in again.</p>

<h4>"A device can only be requested once you are signed in to Vodafone TV"</h4>
<p>Leasing a <code>.wvd</code> needs a signed-in device. Sign in first
(<i>Sign in → Sign in device</i>), then try playback again.</p>

<h4>"The device server is not responding" / "refused the API key"</h4>
<p>A wrong server address or key in the settings, or the server is down. Check
both under <i>Settings → DRM</i>. If the server runs with a self-signed
certificate, also turn on the option not to verify it -- otherwise the addon
refuses to connect, because the verification exchange carries your login
key.</p>

<h4>"No .wvd file -- check the DRM settings"</h4>
<p>The addon found no CDM and has no server configured. Copy a
<code>.wvd</code> into <code>addon_data</code>, point at it in
<i>Settings → DRM</i>, or let it lease a device from the server.</p>

<h4>"The channel could not be unlocked -- it is probably not part of your subscription"</h4>
<p>You are not subscribed to that channel. The gateway reports it as an
internal error, so the addon cannot be completely certain, but in practice this
is nearly always the reason. Unsubscribed channels can be hidden from the list
in the settings.</p>

<h4>"Playback is blocked by another session. Please try again shortly"</h4>
<p>Vodafone allows one playback at a time. Close playback elsewhere (mobile
app, web, TV) and wait a moment -- the server releases the session on its own,
but not instantly.</p>

<h4>"The license server refused the request"</h4>
<p>Usually a <code>.wvd</code> the license server does not accept (see <i>How
do I get my own .wvd</i>), or a CDM that has been blocked. Try a different
<code>.wvd</code>.</p>

<h4>Live TV freezes for a moment, mostly right after changing channel</h4>
<p>The newest segment of a live stream may not be ready on the CDN yet; the
player waits for it and the stream stalls for ~10 s. The <i>Delay behind live</i>
setting handles it (<i>Settings</i>, 5 s by default): the higher it is, the more
stable playback gets and the further behind real time you are. Archive and
recordings are unaffected.</p>

<h4>The QR code will not scan</h4>
<p>The scanner in the Vodafone TV mobile app only reads <b>inverted</b> QR codes
(light on dark), which is exactly how the addon generates them. Scan from
<i>Settings / Account / Sign in on another device</i> in the app. If it still
will not go, sign in with a username and password instead; the result is the
same.</p>

<h4>Where is the log</h4>
<p>Under <i>Settings → Logging</i> turn on request and response logging,
reproduce the problem and send the Kodi log (<code>kodi.log</code>). The addon
writes lines starting with <code>Vodafone TV</code>.</p>
