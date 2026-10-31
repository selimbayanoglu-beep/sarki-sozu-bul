/**
 * Şarkı Sözü Bul — İstemci Uygulaması (app.js)
 *
 * Özellikler:
 * 1. Müzik Modu Mikrofon (echoCancellation: false, noiseSuppression: false) ile
 *    telefonun müziği gürültü sanıp silmesini engeller.
 * 2. Kayıpsız 16-bit PCM WAV Kayan Pencere (Sliding Window) kaydedici ile
 *    kesintisiz Dalga 1 (3.4sn), Dalga 2 (6.8sn), Dalga 3 (10.5sn), Dalga 4 (14.5sn), Dalga 5 (18.5sn)
 *    paketlerini arka planda sunucudaki 4 Katmanlı Motora gönderir.
 * 3. Şarkıyı bulduğu salisede gecikme telafisi (latency compensation) hesaplayarak
 *    şarkının barda TAM O AN çalan saniyesine/satırına kilitlenir ve otomatik kaydırır.
 * 4. Yabancı şarkılar için tek tıkla satır altı Türkçe çeviri sunar.
 */

(() => {
  // DOM Elemanları
  const btnListen = document.getElementById("btnListen");
  const orbIcon = document.getElementById("orbIcon");
  const orbMainText = document.getElementById("orbMainText");
  const orbSubText = document.getElementById("orbSubText");
  const waveCanvas = document.getElementById("waveCanvas");
  const statusMessage = document.getElementById("statusMessage");
  const detectedWordsBox = document.getElementById("detectedWordsBox");
  const detectedWordsText = document.getElementById("detectedWordsText");

  const stepLayer1 = document.getElementById("stepLayer1");
  const stepLayer2 = document.getElementById("stepLayer2");
  const stepLayer3 = document.getElementById("stepLayer3");
  const stepLayer4 = document.getElementById("stepLayer4");

  const httpsBanner = document.getElementById("httpsBanner");
  const httpsRedirectLink = document.getElementById("httpsRedirectLink");
  const btnNativeMicFallback = document.getElementById("btnNativeMicFallback");
  const nativeAudioInput = document.getElementById("nativeAudioInput");

  const searchForm = document.getElementById("searchForm");
  const searchInput = document.getElementById("searchInput");

  const listenSection = document.getElementById("listenSection");
  const lyricsSection = document.getElementById("lyricsSection");
  const trackCover = document.getElementById("trackCover");
  const badgeLayer = document.getElementById("badgeLayer");
  const badgeSync = document.getElementById("badgeSync");
  const trackTitle = document.getElementById("trackTitle");
  const trackArtist = document.getElementById("trackArtist");
  const btnCloseLyrics = document.getElementById("btnCloseLyrics");

  const btnListenAgain = document.getElementById("btnListenAgain");
  const btnStopSync = document.getElementById("btnStopSync");
  const btnTranslate = document.getElementById("btnTranslate");
  const btnAutoScroll = document.getElementById("btnAutoScroll");
  const btnFontDown = document.getElementById("btnFontDown");
  const btnFontUp = document.getElementById("btnFontUp");

  const syncToolbar = document.getElementById("syncToolbar");
  const syncTimerText = document.getElementById("syncTimerText");
  const btnSyncBack1 = document.getElementById("btnSyncBack1");
  const btnSyncBack05 = document.getElementById("btnSyncBack05");
  const btnSyncPause = document.getElementById("btnSyncPause");
  const btnSyncForward05 = document.getElementById("btnSyncForward05");
  const btnSyncForward1 = document.getElementById("btnSyncForward1");

  const lyricsContainer = document.getElementById("lyricsContainer");
  const lyricsLines = document.getElementById("lyricsLines");

  const btnHistory = document.getElementById("btnHistory");
  const historyModal = document.getElementById("historyModal");
  const btnCloseHistory = document.getElementById("btnCloseHistory");
  const btnClearHistory = document.getElementById("btnClearHistory");
  const historyList = document.getElementById("historyList");
  const btnNativeRecord = document.getElementById("btnNativeRecord");

  // Durum Değişkenleri
  let isListening = false;
  let audioCtx = null;
  let mediaStream = null;
  let mediaRecorder = null;
  let recordedMediaChunks = [];
  let sourceNode = null;
  let processorNode = null;
  let analyserNode = null;
  let pcmChunks = [];
  let sampleRate = 44100;
  let waveTimers = [];
  let animFrameId = null;
  let currentAbortControllers = [];

  // Karaoke & Söz Durumu
  const KARAOKE_LEAD_SEC = 1.1; // Karaoke öncü gösterim: sözler söylenmeden 1.1 sn önce parlar ve hazır olur
  let currentSong = null;
  let translatedLinesCache = null;
  let showTranslation = false;
  let autoScrollEnabled = true;
  let syncPaused = false;
  let isSongFinished = false;
  let syncStartPerfTime = 0;
  let syncBaseOffsetSec = 0;
  let syncIntervalId = null;
  let activeLineIdx = -1;
  let plainScrollIntervalId = null;
  let fontSizeRem = 1.45;

  // Sunucu durumu ve HTTPS kontrolü
  let serverStatus = { https_port: 8892, tunnel_url: "" };

  async function checkServerAndHttps() {
    async function fetchStatus() {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        if (data && data.ok) {
          serverStatus = data;
          if (data.tunnel_url) {
            httpsRedirectLink.href = data.tunnel_url;
            httpsRedirectLink.textContent = "🔒 Canlı Mikrofon İçin Aç (HTTPS)";
            return true;
          }
        }
      } catch (_) {}
      return false;
    }

    const hasTunnel = await fetchStatus();

    const isLocal =
      location.hostname === "localhost" ||
      location.hostname === "127.0.0.1";
    const isSecure = window.isSecureContext || location.protocol === "https:" || isLocal;

    if (!isSecure) {
      httpsBanner.classList.remove("hidden");
      if (!hasTunnel) {
        // Tünel hazırlanıyorsa 2 sn aralıklarla kontrol et
        let attempts = 0;
        const interval = setInterval(async () => {
          attempts++;
          const found = await fetchStatus();
          if (found || attempts >= 8) clearInterval(interval);
        }, 2000);
      }
    }
  }

  // Katman Göstergelerini Güncelle
  function setLayerPills(wave) {
    stepLayer1.classList.toggle("active", wave >= 1);
    stepLayer2.classList.toggle("active", wave >= 1);
    stepLayer3.classList.toggle("active", wave >= 2);
    stepLayer4.classList.toggle("active", wave >= 1);
  }

  function resetLayerPills() {
    stepLayer1.classList.remove("active");
    stepLayer2.classList.remove("active");
    stepLayer3.classList.remove("active");
    stepLayer4.classList.remove("active");
  }

  // Float32 PCM dizisini 16-bit standart WAV Blob'una dönüştürür (Kayıpsız ve parmak izi frekanslarını tam koruyarak)
  function encodeWAV(samples, rate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    function writeString(offset, str) {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i));
      }
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true); // PCM chunk size
    view.setUint16(20, 1, true);  // PCM format (uncompressed)
    view.setUint16(22, 1, true);  // Mono
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true); // Byte rate (rate * 1 channel * 2 bytes)
    view.setUint16(32, 2, true);        // Block align
    view.setUint16(34, 16, true);       // Bits per sample
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  // Son N saniyelik ses örneklerini tek bir Float32Array olarak alır
  function getRecentSamples(seconds) {
    const neededSamples = Math.floor(seconds * sampleRate);
    let totalLen = 0;
    for (let i = 0; i < pcmChunks.length; i++) {
      totalLen += pcmChunks[i].length;
    }
    const merged = new Float32Array(totalLen);
    let offset = 0;
    for (let i = 0; i < pcmChunks.length; i++) {
      merged.set(pcmChunks[i], offset);
      offset += pcmChunks[i].length;
    }
    if (merged.length <= neededSamples) {
      return merged;
    }
    return merged.slice(merged.length - neededSamples);
  }

  // Canlı Frekans Dalgası Çizimi
  function drawVisualizer() {
    const ctx = waveCanvas.getContext("2d");
    const width = waveCanvas.width;
    const height = waveCanvas.height;
    const centerX = width / 2;
    const centerY = height / 2;
    const baseRadius = 108;

    const freqData = new Uint8Array(analyserNode ? analyserNode.frequencyBinCount : 64);

    function renderFrame() {
      if (!isListening) {
        ctx.clearRect(0, 0, width, height);
        return;
      }
      animFrameId = requestAnimationFrame(renderFrame);
      if (analyserNode) {
        analyserNode.getByteFrequencyData(freqData);
      }

      ctx.clearRect(0, 0, width, height);

      const bars = 48;
      for (let i = 0; i < bars; i++) {
        const val = freqData[i] || 0;
        const barHeight = 6 + (val / 255) * 38;
        const angle = (i / bars) * Math.PI * 2;

        const x1 = centerX + Math.cos(angle) * baseRadius;
        const y1 = centerY + Math.sin(angle) * baseRadius;
        const x2 = centerX + Math.cos(angle) * (baseRadius + barHeight);
        const y2 = centerY + Math.sin(angle) * (baseRadius + barHeight);

        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.strokeStyle = i % 2 === 0 ? "rgba(0, 229, 255, 0.85)" : "rgba(255, 61, 113, 0.85)";
        ctx.lineWidth = 4;
        ctx.lineCap = "round";
        ctx.stroke();
      }
    }

    renderFrame();
  }

  // Sunucuya Ses Paketi Gönder
  async function sendAudioWave(waveNum, durationSec) {
    if (!isListening) return;

    setLayerPills(waveNum);
    const waveDescriptions = {
      1: "Dalga 1/5: Ses güçlendirme & Shazam parmak izi taranıyor...",
      2: "Dalga 2/5: YouTube Music & Shazam paralel taranıyor...",
      3: "Dalga 3/5: AI Vokal söz yakalama & YouTube Music söz eşleştirme...",
      4: "Dalga 4/5: DJ Hız/Pitch varyasyonları analiz ediliyor...",
      5: "Dalga 5/5: Kapsamlı katalog taraması yapılıyor...",
    };
    statusMessage.textContent = waveDescriptions[waveNum] || `Dalga ${waveNum} taranıyor...`;
    orbSubText.textContent = `Dalga ${waveNum} analiz ediliyor...`;

    let uploadBlob = null;
    let uploadExt = ".wav";
    let uploadMime = "audio/wav";

    // MediaRecorder veri topladıysa onu kullan (telefonun donanım ses kodlayıcısı)
    if (recordedMediaChunks.length > 0) {
      const mime = (mediaRecorder && mediaRecorder.mimeType) || "audio/webm";
      uploadBlob = new Blob(recordedMediaChunks, { type: mime });
      uploadMime = mime;
      uploadExt = mime.includes("mp4") ? ".mp4" : ".webm";
    }

    // MediaRecorder boşsa veya desteklenmiyorsa PCM WAV yedeği gönder
    if (!uploadBlob || uploadBlob.size < 1000) {
      const samples = getRecentSamples(durationSec);
      if (samples.length < sampleRate * 1.0) {
        return;
      }
      uploadBlob = encodeWAV(samples, sampleRate);
      uploadExt = ".wav";
      uploadMime = "audio/wav";
    }

    const waveCapturedAt = performance.now();
    const controller = new AbortController();
    currentAbortControllers.push(controller);

    try {
      const resp = await fetch("/api/recognize", {
        method: "POST",
        headers: {
          "Content-Type": uploadMime,
          "X-Wave": String(waveNum),
          "X-File-Ext": uploadExt,
          "X-Lang": "auto",
        },
        body: uploadBlob,
        signal: controller.signal,
      });

      const data = await resp.json();
      if (!isListening) return;

      if (data && data.ok && data.result) {
        const res = data.result;

        // Müziğin içinden kelimeler duyulduysa ekranda canlı göster
        if (res.detected_words && res.detected_words.length > 0) {
          detectedWordsBox.classList.remove("hidden");
          detectedWordsText.textContent = `"${res.detected_words.join(" / ")}"`;
        } else if (res.detected_vocal_phrase) {
          detectedWordsBox.classList.remove("hidden");
          detectedWordsText.textContent = `"${res.detected_vocal_phrase}"`;
        }

        if (res.recognized && res.title) {
          // ŞARKI BULUNDU! Gecikme telafisini tam ve doğru hesapla:
          // Shazam'ın döndürdüğü offset, gönderilen ses kesitinin BAŞLANGIÇ saniyesidir.
          // O kesitin başından cevabın geldiği şu ana kadar geçen toplam süre: durationSec + latencySec
          const latencySec = (performance.now() - waveCapturedAt) / 1000.0;
          const totalElapsed = durationSec + latencySec;
          if (typeof res.offset_seconds === "number") {
            res.offset_seconds = Math.max(0, res.offset_seconds + totalElapsed);
          }
          stopListening(true);
          if (navigator.vibrate) {
            navigator.vibrate([80, 40, 130]);
          }
          renderRecognizedSong(res);
          return;
        }
      }

      if (waveNum >= 5 && isListening) {
        stopListening(false);
        statusMessage.textContent =
          "Bu kesitte eşleşme bulunamadı. Müziğin vokal/nakarat kısmında tekrar dokunun veya duyduğunuz 2 kelimeyi aşağıya yazın.";
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      console.error("Dalga gönderim hatası:", err);
    }
  }

  // Tek Tuşla Bulana Kadar Dinlemeyi Başlat
  async function startListening() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      if (serverStatus && serverStatus.tunnel_url) {
        statusMessage.textContent = "🔒 Canlı mikrofon için güvenli HTTPS adresine geçiliyor...";
        location.href = serverStatus.tunnel_url;
        return;
      }
      statusMessage.textContent = "🎙️ Telefon mikrofonu açılıyor, 4-5 saniye müziği kaydedip 'Bitti' deyin...";
      nativeAudioInput.click();
      return;
    }

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          autoGainControl: true,
        },
      });
    } catch (_) {
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        console.warn("Mikrofon açılamadı:", err);
        httpsBanner.classList.remove("hidden");
        statusMessage.textContent =
          "Mikrofon izni alınamadı. HTTPS bağlantısını kullanın veya 'Telefonun Kendi Kaydedicisiyle Dinle' butonuna basın.";
        return;
      }
    }

    isListening = true;
    pcmChunks = [];
    recordedMediaChunks = [];
    currentAbortControllers = [];
    detectedWordsBox.classList.add("hidden");
    detectedWordsText.textContent = "";

    // Donanım seviyesinde MediaRecorder (iOS ve Android için kayıpsız ses yakalama)
    mediaRecorder = null;
    if (typeof MediaRecorder !== "undefined") {
      let mime = "";
      if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
        mime = "audio/webm;codecs=opus";
      } else if (MediaRecorder.isTypeSupported("audio/mp4")) {
        mime = "audio/mp4";
      } else if (MediaRecorder.isTypeSupported("audio/webm")) {
        mime = "audio/webm";
      }
      try {
        mediaRecorder = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : undefined);
        mediaRecorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) {
            recordedMediaChunks.push(e.data);
          }
        };
        mediaRecorder.start(400);
      } catch (e) {
        console.warn("MediaRecorder başlatılamadı:", e);
      }
    }

    btnListen.classList.add("listening");
    orbIcon.textContent = "📡";
    orbMainText.textContent = "DİNLENİYOR...";
    orbSubText.textContent = "Durdurmak için dokunun";
    statusMessage.textContent = "Müzik taranıyor (Shazam & YouTube Music)...";
    setLayerPills(1);

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AudioContextClass();
    if (audioCtx.state === "suspended") {
      try {
        await audioCtx.resume();
      } catch (_) {}
    }
    sampleRate = audioCtx.sampleRate || 44100;

    sourceNode = audioCtx.createMediaStreamSource(mediaStream);
    analyserNode = audioCtx.createAnalyser();
    analyserNode.fftSize = 128;

    processorNode = audioCtx.createScriptProcessor(4096, 1, 1);
    processorNode.onaudioprocess = (e) => {
      if (!isListening) return;
      const input = e.inputBuffer.getChannelData(0);
      pcmChunks.push(new Float32Array(input));
      const maxChunks = Math.ceil((25 * sampleRate) / 4096);
      if (pcmChunks.length > maxChunks) {
        pcmChunks.shift();
      }
    };

    sourceNode.connect(analyserNode);
    analyserNode.connect(processorNode);
    processorNode.connect(audioCtx.destination);

    drawVisualizer();

    // Kayan Pencere Dalga Zamanlayıcıları
    waveTimers = [
      setTimeout(() => sendAudioWave(1, 3.8), 3400),
      setTimeout(() => sendAudioWave(2, 5.5), 6200),
      setTimeout(() => sendAudioWave(3, 7.5), 9800),
      setTimeout(() => sendAudioWave(4, 9.5), 13800),
      setTimeout(() => sendAudioWave(5, 12.0), 18000),
    ];
  }

  function stopListening(found = false) {
    isListening = false;

    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      try {
        mediaRecorder.stop();
      } catch (_) {}
    }
    mediaRecorder = null;
    recordedMediaChunks = [];

    waveTimers.forEach((t) => clearTimeout(t));
    waveTimers = [];

    currentAbortControllers.forEach((c) => {
      try {
        c.abort();
      } catch (_) {}
    });
    currentAbortControllers = [];

    if (animFrameId) {
      cancelAnimationFrame(animFrameId);
      animFrameId = null;
    }

    if (processorNode) {
      try {
        processorNode.disconnect();
      } catch (_) {}
      processorNode = null;
    }
    if (sourceNode) {
      try {
        sourceNode.disconnect();
      } catch (_) {}
      sourceNode = null;
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
    if (audioCtx) {
      try {
        audioCtx.close();
      } catch (_) {}
      audioCtx = null;
    }

    btnListen.classList.remove("listening");
    orbIcon.textContent = "🎧";
    orbMainText.textContent = "DOKUN & DİNLET";
    orbSubText.textContent = "Bulana kadar otomatik dinler";

    if (!found) {
      resetLayerPills();
    }
  }

  // Saniyeyi 01:23 formatına çevirir
  function formatTime(sec) {
    if (typeof sec !== "number" || isNaN(sec) || sec < 0) return "00:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // Bulunan Şarkıyı ve Sözlerini Sahne Ekranında Göster
  function renderRecognizedSong(song) {
    currentSong = song;
    translatedLinesCache = null;
    showTranslation = false;
    btnTranslate.classList.remove("active");
    btnTranslate.textContent = "🇹🇷 Türkçe Çeviri";

    listenSection.classList.add("hidden");
    lyricsSection.classList.remove("hidden");

    trackCover.src = song.cover || "icon.svg";
    trackTitle.textContent = song.title || "Bilinmeyen Şarkı";
    trackArtist.textContent = song.artist || "";
    badgeLayer.textContent = song.layer_used || song.source || "4 Katmanlı Motor";

    if (song.synced) {
      badgeSync.textContent = "⚡ Canlı Senkronize Karaoke";
      badgeSync.classList.remove("hidden");
      syncToolbar.classList.remove("hidden");
    } else {
      badgeSync.textContent = "📜 Tam Şarkı Sözleri";
      syncToolbar.classList.add("hidden");
    }

    renderLyricsDOM();
    startKaraokeSync();
  }

  function renderLyricsDOM() {
    lyricsLines.innerHTML = "";
    const lines = (currentSong && currentSong.lines) || [];

    if (lines.length === 0) {
      const emptyP = document.createElement("p");
      emptyP.className = "empty-state";
      emptyP.textContent =
        "Şarkı tanındı ancak bu parçanın metin sözleri veritabanında bulunamadı.";
      lyricsLines.appendChild(emptyP);
      return;
    }

    lines.forEach((lineObj, idx) => {
      const div = document.createElement("div");
      div.className = "lyric-line";
      div.dataset.index = String(idx);

      if (typeof lineObj.time === "number") {
        const timeSpan = document.createElement("span");
        timeSpan.className = "lyric-time-tag";
        timeSpan.textContent = formatTime(lineObj.time);
        div.appendChild(timeSpan);
      }

      const textSpan = document.createElement("span");
      textSpan.textContent = lineObj.text || "♪ • • • ♪";
      div.appendChild(textSpan);

      if (showTranslation && translatedLinesCache && translatedLinesCache[idx]) {
        const trSpan = document.createElement("span");
        trSpan.className = "lyric-tr-sub";
        trSpan.textContent = translatedLinesCache[idx];
        div.appendChild(trSpan);
      }

      // Satıra dokunulduğunda canlı senkronu o satıra hizala (karaoke için 1.1s önceden başlat)
      div.addEventListener("click", () => {
        if (currentSong && currentSong.synced && typeof lineObj.time === "number") {
          syncBaseOffsetSec = Math.max(0, lineObj.time - KARAOKE_LEAD_SEC);
          syncStartPerfTime = performance.now();
          updateSyncedHighlight();
        } else {
          highlightLineByIndex(idx, true);
        }
      });

      lyricsLines.appendChild(div);
    });
  }

  function highlightLineByIndex(idx, smoothScroll = true) {
    const children = lyricsLines.children;
    if (!children || children.length === 0) return;

    for (let i = 0; i < children.length; i++) {
      children[i].classList.toggle("past", i < idx);
      children[i].classList.toggle("active", i === idx);
    }

    activeLineIdx = idx;
    if (autoScrollEnabled && idx >= 0 && idx < children.length) {
      const activeEl = children[idx];
      const containerHeight = lyricsContainer.clientHeight;
      // Aktif satırı ekranın üst üçte birlik hizasına getir ki yaklaşan satırlar rahatça okunsun
      const targetScroll =
        activeEl.offsetTop - lyricsContainer.offsetTop - containerHeight * 0.28;
      lyricsContainer.scrollTo({
        top: Math.max(0, targetScroll),
        behavior: smoothScroll ? "smooth" : "auto",
      });
    }
  }

  function stopKaraokeSync() {
    if (syncIntervalId) {
      clearInterval(syncIntervalId);
      syncIntervalId = null;
    }
    if (plainScrollIntervalId) {
      clearInterval(plainScrollIntervalId);
      plainScrollIntervalId = null;
    }
  }

  function getCurrentPlaybackSec() {
    if (syncPaused) return syncBaseOffsetSec;
    return syncBaseOffsetSec + (performance.now() - syncStartPerfTime) / 1000.0;
  }

  function updateSyncedHighlight() {
    if (!currentSong || !currentSong.synced || !currentSong.lines) return;
    const nowSec = getCurrentPlaybackSec();
    syncTimerText.textContent = formatTime(nowSec);

    const lines = currentSong.lines;
    const lastLine = lines[lines.length - 1];
    const lastLineTime = (lastLine && typeof lastLine.time === "number") ? lastLine.time : (currentSong.duration || 240);

    // Şarkı tamamlandıysa (son satırdan 6 saniye sonra veya süre aşıldığında) akışı durdur
    if (nowSec >= lastLineTime + 6) {
      isSongFinished = true;
      stopKaraokeSync();
      syncTimerText.textContent = `${formatTime(lastLineTime)} (Tamamlandı)`;
      btnSyncPause.textContent = "🔄 Baştan";
      if (btnStopSync) btnStopSync.textContent = "🔄 Baştan";
      highlightLineByIndex(lines.length - 1, true);
      return;
    }

    let foundIdx = -1;
    // Karaoke Modu: Satırın vuruşu (t) gelmeden 1.1 saniye önce ekranda aktif hale getir!
    for (let i = 0; i < lines.length; i++) {
      const t = lines[i].time;
      if (typeof t === "number" && t <= nowSec + KARAOKE_LEAD_SEC) {
        foundIdx = i;
      } else if (typeof t === "number" && t > nowSec + KARAOKE_LEAD_SEC) {
        break;
      }
    }

    if (foundIdx !== -1 && foundIdx !== activeLineIdx) {
      highlightLineByIndex(foundIdx, true);
    }
  }

  function startKaraokeSync() {
    stopKaraokeSync();
    activeLineIdx = -1;
    syncPaused = false;
    isSongFinished = false;
    btnSyncPause.textContent = "⏸ Duraklat";
    if (btnStopSync) btnStopSync.textContent = "⏹️ Akışı Durdur";

    if (!currentSong || !currentSong.lines || currentSong.lines.length === 0) {
      return;
    }

    // Başlangıç satırı veya saniyesi varsa oradan başlat
    const initialIdx =
      typeof currentSong.matched_line_index === "number" &&
      currentSong.matched_line_index >= 0
        ? currentSong.matched_line_index
        : 0;

    if (currentSong.synced) {
      if (typeof currentSong.offset_seconds === "number" && currentSong.offset_seconds > 0) {
        syncBaseOffsetSec = currentSong.offset_seconds;
      } else if (
        currentSong.lines[initialIdx] &&
        typeof currentSong.lines[initialIdx].time === "number"
      ) {
        syncBaseOffsetSec = currentSong.lines[initialIdx].time;
      } else {
        syncBaseOffsetSec = 0;
      }
      syncStartPerfTime = performance.now();
      updateSyncedHighlight();
      syncIntervalId = setInterval(updateSyncedHighlight, 200);
    } else {
      // Senkronize (LRC) zaman damgası olmayan düz şarkılarda yakalanan satıra odaklan
      // ve her 4.5 saniyede bir sonraki satıra yumuşakça ilerle
      highlightLineByIndex(initialIdx, false);
      plainScrollIntervalId = setInterval(() => {
        if (!autoScrollEnabled || syncPaused || isSongFinished) return;
        const nextIdx = activeLineIdx + 1;
        if (currentSong && currentSong.lines) {
          if (nextIdx < currentSong.lines.length) {
            highlightLineByIndex(nextIdx, true);
          } else {
            // Şarkı sözlerinin sonuna ulaşıldı
            isSongFinished = true;
            stopKaraokeSync();
            if (btnStopSync) btnStopSync.textContent = "🔄 Baştan";
            btnSyncPause.textContent = "🔄 Baştan";
          }
        }
      }, 4500);
    }
  }

  // Türkçe Çeviri Düğmesi
  btnTranslate.addEventListener("click", async () => {
    if (!currentSong || !currentSong.lines || currentSong.lines.length === 0) return;

    if (showTranslation) {
      showTranslation = false;
      btnTranslate.classList.remove("active");
      btnTranslate.textContent = "🇹🇷 Türkçe Çeviri";
      renderLyricsDOM();
      if (activeLineIdx >= 0) highlightLineByIndex(activeLineIdx, false);
      return;
    }

    if (translatedLinesCache) {
      showTranslation = true;
      btnTranslate.classList.add("active");
      btnTranslate.textContent = "🇹🇷 Çeviri Açık";
      renderLyricsDOM();
      if (activeLineIdx >= 0) highlightLineByIndex(activeLineIdx, false);
      return;
    }

    btnTranslate.textContent = "⏳ Çevriliyor...";
    try {
      const resp = await fetch("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lines: currentSong.lines,
          target_lang: "tr",
        }),
      });
      const data = await resp.json();
      if (data && data.ok && Array.isArray(data.translated_lines)) {
        translatedLinesCache = data.translated_lines;
        showTranslation = true;
        btnTranslate.classList.add("active");
        btnTranslate.textContent = "🇹🇷 Çeviri Açık";
        renderLyricsDOM();
        if (activeLineIdx >= 0) highlightLineByIndex(activeLineIdx, false);
      } else {
        btnTranslate.textContent = "🇹🇷 Türkçe Çeviri";
      }
    } catch (_) {
      btnTranslate.textContent = "🇹🇷 Türkçe Çeviri";
    }
  });

  // Otomatik Akış ve Punto Kontrolleri
  btnAutoScroll.addEventListener("click", () => {
    autoScrollEnabled = !autoScrollEnabled;
    btnAutoScroll.classList.toggle("active", autoScrollEnabled);
  });

  btnFontUp.addEventListener("click", () => {
    fontSizeRem = Math.min(2.4, +(fontSizeRem + 0.15).toFixed(2));
    document.documentElement.style.setProperty("--lyric-font-size", `${fontSizeRem}rem`);
  });

  btnFontDown.addEventListener("click", () => {
    fontSizeRem = Math.max(1.0, +(fontSizeRem - 0.15).toFixed(2));
    document.documentElement.style.setProperty("--lyric-font-size", `${fontSizeRem}rem`);
  });

  // Zaman İnce Ayar Kontrolleri (-1s / -0.5s / Duraklat / +0.5s / +1s)
  function adjustSync(deltaSec) {
    syncBaseOffsetSec = Math.max(0, getCurrentPlaybackSec() + deltaSec);
    syncStartPerfTime = performance.now();
    updateSyncedHighlight();
  }

  if (btnSyncBack1) btnSyncBack1.addEventListener("click", () => adjustSync(-1.0));
  if (btnSyncBack05) btnSyncBack05.addEventListener("click", () => adjustSync(-0.5));
  if (btnSyncForward05) btnSyncForward05.addEventListener("click", () => adjustSync(0.5));
  if (btnSyncForward1) btnSyncForward1.addEventListener("click", () => adjustSync(1.0));

  // Şarkı Ekranını Kapatıp Ana Ekrana Dön
  function closeLyricsView() {
    stopKaraokeSync();
    currentSong = null;
    lyricsSection.classList.add("hidden");
    listenSection.classList.remove("hidden");
    statusMessage.textContent = "Hazır. Şarkıyı dinletmek için dokunun veya arama kutusuna yazın.";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  if (btnCloseLyrics) {
    btnCloseLyrics.addEventListener("click", closeLyricsView);
  }

  // Akışı Durdur / Başlat Fonksiyonu
  function toggleSyncFlow() {
    if (isSongFinished) {
      isSongFinished = false;
      syncBaseOffsetSec = 0;
      syncStartPerfTime = performance.now();
      syncPaused = false;
      btnSyncPause.textContent = "⏸ Duraklat";
      if (btnStopSync) btnStopSync.textContent = "⏹️ Akışı Durdur";
      startKaraokeSync();
      return;
    }

    if (!syncPaused) {
      syncBaseOffsetSec = getCurrentPlaybackSec();
      syncPaused = true;
      btnSyncPause.textContent = "▶️ Devam Et";
      if (btnStopSync) btnStopSync.textContent = "▶ Akışı Başlat";
    } else {
      syncStartPerfTime = performance.now();
      syncPaused = false;
      btnSyncPause.textContent = "⏸ Duraklat";
      if (btnStopSync) btnStopSync.textContent = "⏹️ Akışı Durdur";
    }
  }

  if (btnStopSync) {
    btnStopSync.addEventListener("click", toggleSyncFlow);
  }

  btnSyncPause.addEventListener("click", toggleSyncFlow);

  // Yeni Şarkı Dinle
  btnListenAgain.addEventListener("click", () => {
    closeLyricsView();
    startListening();
  });

  // Ana Dinleme Küresi Tıklaması
  btnListen.addEventListener("click", () => {
    if (isListening) {
      stopListening(false);
      statusMessage.textContent = "Dinleme durduruldu. Tekrar dinlemek için dokunun.";
    } else {
      startListening();
    }
  });

  // Yedek Native Mikrofon Kaydedici (Donanım Seviyesinde Ses Kaydı)
  if (btnNativeRecord) {
    btnNativeRecord.addEventListener("click", () => {
      nativeAudioInput.click();
    });
  }

  btnNativeMicFallback.addEventListener("click", () => {
    nativeAudioInput.click();
  });

  nativeAudioInput.addEventListener("change", async () => {
    const file = nativeAudioInput.files && nativeAudioInput.files[0];
    if (!file) return;

    statusMessage.textContent = "Ses kaydı 4 katmanlı motorda analiz ediliyor...";
    setLayerPills(2);

    const extMatch = (file.name || "").match(/\.[a-zA-Z0-9]+$/);
    const ext = extMatch ? extMatch[0] : ".m4a";

    try {
      const resp = await fetch("/api/recognize", {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Wave": "2",
          "X-File-Ext": ext,
          "X-Lang": "auto",
        },
        body: file,
      });
      const data = await resp.json();
      resetLayerPills();
      if (data && data.ok && data.result && data.result.recognized) {
        renderRecognizedSong(data.result);
      } else {
        statusMessage.textContent =
          "Kayıttan şarkı çıkarılamadı. Müziğe daha yakın tutup 5-6 saniye kaydedin.";
      }
    } catch (_) {
      resetLayerPills();
      statusMessage.textContent = "Sunucuya gönderilirken hata oluştu.";
    }
    nativeAudioInput.value = "";
  });

  // Hızlı Söz / İsim Arama Formu
  searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = (searchInput.value || "").trim();
    if (!q) return;

    statusMessage.textContent = `"${q}" için şarkı ve sözler aranıyor...`;
    try {
      const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const data = await resp.json();
      if (data && data.ok && data.result) {
        searchInput.value = "";
        renderRecognizedSong(data.result);
      } else {
        statusMessage.textContent =
          data.error || "Eşleşen şarkı bulunamadı. Farklı birkaç kelime deneyin.";
      }
    } catch (_) {
      statusMessage.textContent = "Arama sırasında bağlantı hatası oluştu.";
    }
  });

  // Geçmiş Şarkılar Çekmecesi
  async function loadHistoryModal() {
    historyModal.classList.remove("hidden");
    historyList.innerHTML = '<p class="empty-state">Yükleniyor...</p>';
    try {
      const resp = await fetch("/api/history");
      const data = await resp.json();
      const items = (data && data.items) || [];
      if (items.length === 0) {
        historyList.innerHTML = '<p class="empty-state">Henüz bulunan şarkı yok.</p>';
        return;
      }
      historyList.innerHTML = "";
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "history-item";
        row.innerHTML = `
          <img src="${item.cover || "icon.svg"}" alt="Kapak" />
          <div class="history-item-info">
            <div class="history-item-title">${item.title || ""}</div>
            <div class="history-item-sub">${item.artist || ""} • ${item.timestamp || ""}</div>
          </div>
        `;
        row.addEventListener("click", () => {
          historyModal.classList.add("hidden");
          renderRecognizedSong(item);
        });
        historyList.appendChild(row);
      });
    } catch (_) {
      historyList.innerHTML = '<p class="empty-state">Geçmiş yüklenemedi.</p>';
    }
  }

  btnHistory.addEventListener("click", loadHistoryModal);
  btnCloseHistory.addEventListener("click", () => historyModal.classList.add("hidden"));
  historyModal.addEventListener("click", (e) => {
    if (e.target === historyModal) historyModal.classList.add("hidden");
  });

  btnClearHistory.addEventListener("click", async () => {
    await fetch("/api/history", { method: "DELETE" });
    historyList.innerHTML = '<p class="empty-state">Geçmiş temizlendi.</p>';
  });

  // Başlangıçta sunucu ve HTTPS durumunu kontrol et
  checkServerAndHttps();
})();
