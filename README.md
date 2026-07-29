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
