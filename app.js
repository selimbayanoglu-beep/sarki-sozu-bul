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

  // Canlı Telefon Mikrofonu ile Senkron Takibi (Sözlerin internet saatine değil telefondaki sese kilitlenmesi)
  let isLiveMicSyncing = false;
  let liveMicSyncTimerId = null;
  let liveSilenceCheckId = null;
  let autoSilencePaused = false;
  let lastSoundHeardPerfTime = 0;
  let isSyncAudioInFlight = false;

  // Sunucu durumu, HTTPS kontrolü ve Otomatik Güncel Motor Yönlendirmesi
  let serverStatus = { https_port: 8892, tunnel_url: "" };
  const UPGRADED_TUNNEL_API = "https://poll-maryland-programming-skirt.trycloudflare.com";
  let activeApiBase =
    location.hostname === "159.195.255.168" ||
    location.hostname === "lately-nokia-removable-cups.trycloudflare.com"
      ? UPGRADED_TUNNEL_API
      : "";

  async function apiFetch(path, options = {}) {
    const primaryUrl = `${activeApiBase}${path}`;
    try {
      const resp = await fetch(primaryUrl, options);
      if (resp.ok || !activeApiBase) {
        return resp;
      }
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
    }
    return fetch(path, options);
  }

  async function checkServerAndHttps() {
    async function fetchStatus() {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        if (data && data.ok) {
          serverStatus = data;
          // Eğer mevcut sunucuda YouTube Music motoru henüz aktif değilse (konteyner restart edilmediyse)
          // API isteklerini otomatik olarak güncel canlı motora yönlendir
          if (!data.engines || !data.engines.ytmusic) {
            try {
              const upRes = await fetch(`${UPGRADED_TUNNEL_API}/api/status`);
              const upData = await upRes.json();
              if (upData && upData.ok && upData.engines && upData.engines.ytmusic) {
                activeApiBase = UPGRADED_TUNNEL_API;
              }
            } catch (_) {}
          }
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

  // Uzak mikrofon sinyallerini kırpmadan (clipping yapmadan) ideal seviyeye yükseltir
  function normalizeSamples(samples) {
    let maxPeak = 0;
    for (let i = 0; i < samples.length; i++) {
      const abs = Math.abs(samples[i]);
      if (abs > maxPeak) maxPeak = abs;
    }
    if (maxPeak <= 0.0001 || maxPeak >= 0.55) {
      return { samples, maxPeak };
    }
    const gain = Math.min(24.0, 0.82 / maxPeak);
    const out = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      out[i] = Math.max(-1, Math.min(1, samples[i] * gain));
    }
    return { samples: out, maxPeak };
  }

  // MediaRecorder segmentini sonlandırıp geçerli bir kapsayıcı (MP4/WebM) üretir ve yenisini başlatır
  function flushMediaRecorderBlob() {
    return new Promise((resolve) => {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        if (recordedMediaChunks.length > 0) {
          const mime = (mediaRecorder && mediaRecorder.mimeType) || "audio/webm";
          resolve({
            blob: new Blob(recordedMediaChunks, { type: mime }),
            mime,
            ext: mime.includes("mp4") ? ".mp4" : ".webm",
          });
        } else {
          resolve(null);
        }
        return;
      }
      const mime = mediaRecorder.mimeType || "audio/webm";
      const onStop = () => {
        const chunks = recordedMediaChunks.slice();
        recordedMediaChunks = [];
        if (isListening && mediaStream) {
          try {
            mediaRecorder = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : undefined);
            mediaRecorder.ondataavailable = (e) => {
              if (e.data && e.data.size > 0) recordedMediaChunks.push(e.data);
            };
            mediaRecorder.start(400);
          } catch (_) {}
        }
        if (chunks.length > 0) {
          resolve({
            blob: new Blob(chunks, { type: mime }),
            mime,
            ext: mime.includes("mp4") ? ".mp4" : ".webm",
          });
        } else {
          resolve(null);
        }
      };
      mediaRecorder.addEventListener("stop", onStop, { once: true });
      try {
        mediaRecorder.stop();
      } catch (_) {
        resolve(null);
      }
    });
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

    // Öncelik 1: Kayıpsız kayan pencere PCM WAV (her zaman son durationSec saniyeyi ve geçerli RIFF başlığını garantiler)
    const rawSamples = getRecentSamples(durationSec);
    if (rawSamples.length >= sampleRate * 1.0) {
      const { samples: normSamples, maxPeak } = normalizeSamples(rawSamples);
      if (maxPeak > 0.0001) {
        uploadBlob = encodeWAV(normSamples, sampleRate);
        uploadExt = ".wav";
        uploadMime = "audio/wav";
      }
    }

    // Öncelik 2: WebAudio sessizse donanım MediaRecorder kapsayıcısını sonlandırıp gönder
    if (!uploadBlob || uploadBlob.size < 1000) {
      const flushed = await flushMediaRecorderBlob();
      if (flushed && flushed.blob && flushed.blob.size > 500) {
        uploadBlob = flushed.blob;
        uploadMime = flushed.mime;
        uploadExt = flushed.ext;
      } else if (rawSamples.length >= sampleRate * 0.5) {
        uploadBlob = encodeWAV(rawSamples, sampleRate);
        uploadExt = ".wav";
        uploadMime = "audio/wav";
      }
    }

    if (!uploadBlob || uploadBlob.size < 400) {
      return;
    }

    const waveCapturedAt = performance.now();
    const controller = new AbortController();
    currentAbortControllers.push(controller);

    try {
      const resp = await apiFetch("/api/recognize", {
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
          // Sunucu (_shazam_recognize_single), gönderilen ses kesitinin BİTİŞ (waveCapturedAt) saniyesini döndürür.
          // Bu yüzden sadece ağ/işlem süresi kadar (latencySec) ileri sarıyoruz.
          const latencySec = (performance.now() - waveCapturedAt) / 1000.0;
          if (typeof res.offset_seconds === "number") {
            res.offset_seconds = Math.max(0, res.offset_seconds + latencySec);
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

    // iOS Safari ve mobil tarayıcılarda kullanıcı dokunuşu (user gesture) kaybolmadan
    // AudioContext'i getUserMedia öncesinde senkron olarak oluştur ve uyandır
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      try {
        if (!audioCtx || audioCtx.state === "closed") {
          audioCtx = new AudioContextClass();
        }
        if (audioCtx.state === "suspended") {
          audioCtx.resume().catch(() => {});
        }
      } catch (_) {}
    }

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
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

    // Donanım seviyesinde MediaRecorder (iOS ve Android için yedek ses yakalama)
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

    if (!audioCtx || audioCtx.state === "closed") {
      audioCtx = new AudioContextClass();
    }
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
      if (!isListening && !isLiveMicSyncing) return;
      const input = e.inputBuffer.getChannelData(0);
      let sumSq = 0;
      for (let i = 0; i < input.length; i += 4) {
        sumSq += input[i] * input[i];
      }
      const rms = Math.sqrt(sumSq / (input.length / 4));
      if (rms > 0.0022) {
        lastSoundHeardPerfTime = performance.now();
      }
      pcmChunks.push(new Float32Array(input));
      const maxChunks = Math.ceil((25 * sampleRate) / 4096);
      if (pcmChunks.length > maxChunks) {
        pcmChunks.shift();
      }
    };

    // Hoparlörden geri besleme (feedback) yapmadan işlemciyi canlı tutmak için sıfır kazançlı düğüm
    const silentGain = audioCtx.createGain();
    silentGain.gain.value = 0;

    sourceNode.connect(analyserNode);
    analyserNode.connect(processorNode);
    processorNode.connect(silentGain);
    silentGain.connect(audioCtx.destination);
    lastSoundHeardPerfTime = performance.now();

    drawVisualizer();

    // Kayan Pencere Dalga Zamanlayıcıları (Shazam 12sn pencere sınırına tam uyumlu)
    waveTimers = [
      setTimeout(() => sendAudioWave(1, 3.8), 3200),
      setTimeout(() => sendAudioWave(2, 5.5), 5800),
      setTimeout(() => sendAudioWave(3, 7.5), 9000),
      setTimeout(() => sendAudioWave(4, 9.5), 12800),
      setTimeout(() => sendAudioWave(5, 11.2), 16800),
    ];
  }

  function closeMicStream() {
    isLiveMicSyncing = false;
    if (liveMicSyncTimerId) {
      clearInterval(liveMicSyncTimerId);
      liveMicSyncTimerId = null;
    }
    if (liveSilenceCheckId) {
      clearInterval(liveSilenceCheckId);
      liveSilenceCheckId = null;
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

    // Eğer şarkı bulunduysa (found === true), mikrofonu KAPATMA!
    // Çünkü sözlerin internet saatine değil, telefondan gelen canlı sese senkronize olması için mikrofonu dinlemeye devam edeceğiz.
    if (!found) {
      closeMicStream();
      resetLayerPills();
    } else {
      isLiveMicSyncing = true;
      lastSoundHeardPerfTime = performance.now();
    }

    btnListen.classList.remove("listening");
    orbIcon.textContent = "🎧";
    orbMainText.textContent = "DOKUN & DİNLET";
    orbSubText.textContent = "Bulana kadar otomatik dinler";
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

    document.body.classList.add("lyrics-active");
    listenSection.classList.add("hidden");
    lyricsSection.classList.remove("hidden");

    trackCover.src = song.cover || "icon.svg";
    trackTitle.textContent = song.title || "Bilinmeyen Şarkı";
    trackArtist.textContent = song.artist || "";
    badgeLayer.textContent = song.layer_used || song.source || "4 Katmanlı Motor";

    if (song.synced) {
      badgeSync.textContent = mediaStream ? "🎙️ Telefon Sesiyle Canlı Senkron" : "⚡ Canlı Senkronize Karaoke";
      badgeSync.classList.remove("hidden");
      syncToolbar.classList.remove("hidden");
    } else {
      badgeSync.textContent = mediaStream ? "🎙️ Telefon Sesiyle Söz Takibi" : "📜 Tam Şarkı Sözleri";
      badgeSync.classList.remove("hidden");
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
      div.className = "lyric-line upcoming";
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
          updateSyncedHighlight(true);
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
      const el = children[i];
      el.classList.toggle("past", i < idx);
      el.classList.toggle("active", i === idx);
      el.classList.toggle("next-up", i === idx + 1 || i === idx + 2);
      el.classList.toggle("upcoming", i > idx);
    }

    activeLineIdx = idx;
    if (autoScrollEnabled && idx >= 0 && idx < children.length) {
      const activeEl = children[idx];
      const containerHeight = lyricsContainer.clientHeight;
      // Aktif satırı kutunun üst %16 hizasına getir ki altındaki söylenecek sözler ekranda tam görünsün
      let targetScroll = activeEl.offsetTop - containerHeight * 0.16;

      // Söylenen sonraki sözün (idx + 1 ve idx + 2) ekranın altından taşmasını kesin olarak engelle
      const lookaheadEl = children[Math.min(children.length - 1, idx + 2)] || children[idx + 1] || activeEl;
      const lookaheadBottom = lookaheadEl.offsetTop + lookaheadEl.offsetHeight;
      const visibleBottom = Math.max(0, targetScroll) + containerHeight - 24;
      if (lookaheadBottom > visibleBottom) {
        const nextEl = children[idx + 1] || activeEl;
        const nextBottom = nextEl.offsetTop + nextEl.offsetHeight;
        if (nextBottom > visibleBottom) {
          targetScroll += nextBottom - visibleBottom + 16;
        }
      }

      lyricsContainer.scrollTo({
        top: Math.max(0, targetScroll),
        behavior: smoothScroll ? "smooth" : "auto",
      });
    }
  }

  function stopKaraokeSync(closeMic = false) {
    if (syncIntervalId) {
      clearInterval(syncIntervalId);
      syncIntervalId = null;
    }
    if (plainScrollIntervalId) {
      clearInterval(plainScrollIntervalId);
      plainScrollIntervalId = null;
    }
    if (liveMicSyncTimerId) {
      clearInterval(liveMicSyncTimerId);
      liveMicSyncTimerId = null;
    }
    if (liveSilenceCheckId) {
      clearInterval(liveSilenceCheckId);
      liveSilenceCheckId = null;
    }
    autoSilencePaused = false;
    if (closeMic) {
      closeMicStream();
    }
  }

  function getCurrentPlaybackSec() {
    if (syncPaused || autoSilencePaused) return syncBaseOffsetSec;
    return syncBaseOffsetSec + (performance.now() - syncStartPerfTime) / 1000.0;
  }

  // Telefondan gelen canlı sesi periyodik olarak analiz edip sözleri gerçek sese kilitler
  async function pollLiveAudioSync() {
    if (!isLiveMicSyncing || !currentSong || syncPaused || isSongFinished || isSyncAudioInFlight) {
      return;
    }
    const rawSamples = getRecentSamples(4.2);
    if (rawSamples.length < sampleRate * 2.5) return;

    const { samples: normSamples, maxPeak } = normalizeSamples(rawSamples);
    if (maxPeak <= 0.002) {
      return; // Ortamda anlamlı müzik/vokal sinyali yok
    }

    isSyncAudioInFlight = true;
    const capturePerf = performance.now();
    const currentOffsetAtCapture = getCurrentPlaybackSec();
    const wavBlob = encodeWAV(normSamples, sampleRate);

    try {
      const resp = await apiFetch("/api/sync-audio", {
        method: "POST",
        headers: {
          "Content-Type": "audio/wav",
          "X-File-Ext": ".wav",
          "X-Track-Title": encodeURIComponent(currentSong.title || ""),
          "X-Track-Artist": encodeURIComponent(currentSong.artist || ""),
          "X-Current-Offset": String(currentOffsetAtCapture.toFixed(2)),
        },
        body: wavBlob,
      });
      const data = await resp.json();
      if (!isLiveMicSyncing || !currentSong || syncPaused || isSongFinished) return;

      if (data && data.ok && data.result && data.result.synced) {
        const sr = data.result;
        const roundTripSec = (performance.now() - capturePerf) / 1000.0;

        if (currentSong.synced && typeof sr.offset_seconds === "number") {
          const livePhoneSec = Math.max(0, sr.offset_seconds + roundTripSec);
          const currentSec = getCurrentPlaybackSec();
          const drift = livePhoneSec - currentSec;

          // Sadece ileri yönde veya anlamlı sapmalarda yumuşak düzeltme yap;
          // sözlerin nakarat tekrarlarında yukarı-aşağı zıplamasını kesin olarak engelle!
          if (sr.method === "acoustic_live") {
            if (drift > 1.25 && drift < 25.0) {
              syncBaseOffsetSec = currentSec + drift * 0.65;
              syncStartPerfTime = performance.now();
              updateSyncedHighlight(false);
            } else if (drift < -1.8 && drift > -6.0) {
              // Ekran sesten biraz öndeyse satırı geriye zıplatmadan sayacı yavaşça sese yaklaştır
              syncBaseOffsetSec = currentSec + drift * 0.45;
              syncStartPerfTime = performance.now();
            }
            badgeSync.textContent = "🎙️ Telefondaki Müzik Sesiyle Senkron";
          } else if (sr.method === "vocal_live") {
            if (drift > 1.2 && drift < 7.5) {
              syncBaseOffsetSec = currentSec + drift * 0.5;
              syncStartPerfTime = performance.now();
              updateSyncedHighlight(false);
            }
            badgeSync.textContent = "🎤 Telefondaki Vokal Sesiyle Senkron";
          }
        } else if (
          !currentSong.synced &&
          typeof sr.matched_line_index === "number" &&
          sr.matched_line_index > activeLineIdx &&
          sr.matched_line_index <= activeLineIdx + 3
        ) {
          highlightLineByIndex(sr.matched_line_index, true);
          badgeSync.textContent = "🎤 Telefondaki Vokal Sesiyle Senkron";
        }
      }
    } catch (_) {
      // Ağ gecikmesinde mevcut akışı bozma
    } finally {
      isSyncAudioInFlight = false;
    }
  }

  function startLiveMicSync() {
    if (!mediaStream || !audioCtx) return;
    isLiveMicSyncing = true;
    lastSoundHeardPerfTime = performance.now();

    // 1. Telefona gelen müzik kesildiğinde / duraklatıldığında sözlerin boşuna akmasını durdur
    liveSilenceCheckId = setInterval(() => {
      if (!isLiveMicSyncing || syncPaused || isSongFinished) return;
      const silenceMs = performance.now() - lastSoundHeardPerfTime;
      if (!autoSilencePaused && silenceMs > 4200) {
        syncBaseOffsetSec = getCurrentPlaybackSec();
        autoSilencePaused = true;
        badgeSync.textContent = "🔇 Telefona Ses Gelmiyor (Bekleniyor)";
      } else if (autoSilencePaused && silenceMs < 600) {
        syncStartPerfTime = performance.now();
        autoSilencePaused = false;
        badgeSync.textContent = "🎙️ Telefon Sesiyle Canlı Senkron";
      }
    }, 300);

    // 2. Her 4.2 saniyede bir telefondan gelen canlı müzik/vokal ile sözlerin saniyesini hizala
    liveMicSyncTimerId = setInterval(pollLiveAudioSync, 4200);
  }

  function updateSyncedHighlight(allowBackward = false) {
    if (!currentSong || !currentSong.synced || !currentSong.lines) return;
    const nowSec = getCurrentPlaybackSec();
    syncTimerText.textContent = formatTime(nowSec);

    const lines = currentSong.lines;
    const lastLine = lines[lines.length - 1];
    const lastLineTime = (lastLine && typeof lastLine.time === "number") ? lastLine.time : (currentSong.duration || 240);

    // Şarkı tamamlandıysa (son satırdan 6 saniye sonra veya süre aşıldığında) akışı durdur
    if (nowSec >= lastLineTime + 6) {
      isSongFinished = true;
      stopKaraokeSync(true);
      syncTimerText.textContent = `${formatTime(lastLineTime)} (Tamamlandı)`;
      btnSyncPause.textContent = "🔄 Baştan";
      if (btnStopSync) btnStopSync.textContent = "🔄 Baştan";
      highlightLineByIndex(lines.length - 1, true);
      return;
    }

    let foundIdx = 0;
    // Karaoke Modu: Satırın vuruşu (t) gelmeden 1.1 saniye önce ekranda aktif hale getir!
    for (let i = 0; i < lines.length; i++) {
      const t = lines[i].time;
      if (typeof t === "number" && t <= nowSec + KARAOKE_LEAD_SEC) {
        foundIdx = i;
      } else if (typeof t === "number" && t > nowSec + KARAOKE_LEAD_SEC) {
        break;
      }
    }

    // Otomatik akış sırasında (kullanıcı elle geri sarmadıkça) sözlerin yukarı-aşağı zıplamasını engelle
    if (!allowBackward && activeLineIdx >= 0 && foundIdx < activeLineIdx) {
      foundIdx = activeLineIdx;
    }

    if (foundIdx !== activeLineIdx) {
      highlightLineByIndex(foundIdx, true);
    }
  }

  function startKaraokeSync() {
    stopKaraokeSync(false);
    activeLineIdx = -1;
    syncPaused = false;
    autoSilencePaused = false;
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
      syncIntervalId = setInterval(updateSyncedHighlight, 180);
    } else {
      // Senkronize (LRC) zaman damgası olmayan düz şarkılarda yakalanan satıra odaklan
      // ve her 4.5 saniyede bir sonraki satıra yumuşakça ilerle
      highlightLineByIndex(initialIdx, false);
      plainScrollIntervalId = setInterval(() => {
        if (!autoScrollEnabled || syncPaused || autoSilencePaused || isSongFinished) return;
        const nextIdx = activeLineIdx + 1;
        if (currentSong && currentSong.lines) {
          if (nextIdx < currentSong.lines.length) {
            highlightLineByIndex(nextIdx, true);
          } else {
            // Şarkı sözlerinin sonuna ulaşıldı
            isSongFinished = true;
            stopKaraokeSync(true);
            if (btnStopSync) btnStopSync.textContent = "🔄 Baştan";
            btnSyncPause.textContent = "🔄 Baştan";
          }
        }
      }, 4500);
    }

    // Telefondan gelen canlı sesle sürekli senkronizasyonu başlat
    startLiveMicSync();
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
      const resp = await apiFetch("/api/translate", {
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
    updateSyncedHighlight(true);
  }

  if (btnSyncBack1) btnSyncBack1.addEventListener("click", () => adjustSync(-1.0));
  if (btnSyncBack05) btnSyncBack05.addEventListener("click", () => adjustSync(-0.5));
  if (btnSyncForward05) btnSyncForward05.addEventListener("click", () => adjustSync(0.5));
  if (btnSyncForward1) btnSyncForward1.addEventListener("click", () => adjustSync(1.0));

  // Şarkı Ekranını Kapatıp Ana Ekrana Dön
  function closeLyricsView() {
    stopKaraokeSync(true);
    currentSong = null;
    document.body.classList.remove("lyrics-active");
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
      const resp = await apiFetch("/api/recognize", {
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
      const resp = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);
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
      const resp = await apiFetch("/api/history");
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
    await apiFetch("/api/history", { method: "DELETE" });
    historyList.innerHTML = '<p class="empty-state">Geçmiş temizlendi.</p>';
  });

  // Başlangıçta sunucu ve HTTPS durumunu kontrol et
  checkServerAndHttps();
})();
