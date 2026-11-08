/**
 * Şarkı Sözü Bul — İstemci Uygulaması (app.js)
 *
 * Özellikler:
 * 1. Müzik Modu Mikrofon (echoCancellation: false, noiseSuppression: false) + 22.05kHz
 *    Spektrogram Korumalı 16-bit PCM WAV Kayan Pencere (Sliding Window) kaydedici.
 * 2. Tarayıcı içi Canlı Konuşma/Vokal Tanıma (Web Speech API) + Sunucu 4 Katmanlı Motoru.
 * 3. Şarkıyı bulduğu anda gecikme telafisi (latency compensation) ile tam o anki
 *    saniyeye/satıra kilitlenen canlı karaoke akışı.
 * 4. Çoklu aday eşleşmeleri (Diğer Eşleşmeler), ses önizleme (Müziği Çal) ve Türkçe çeviri.
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
  const btnVoiceSearch = document.getElementById("btnVoiceSearch");

  const listenSection = document.getElementById("listenSection");
  const lyricsSection = document.getElementById("lyricsSection");
  const trackCover = document.getElementById("trackCover");
  const badgeLayer = document.getElementById("badgeLayer");
  const badgeSync = document.getElementById("badgeSync");
  const trackTitle = document.getElementById("trackTitle");
  const trackArtist = document.getElementById("trackArtist");
  const btnCloseLyrics = document.getElementById("btnCloseLyrics");

  const candidatesBar = document.getElementById("candidatesBar");
  const candidatesList = document.getElementById("candidatesList");

  const btnListenAgain = document.getElementById("btnListenAgain");
  const btnPreviewAudio = document.getElementById("btnPreviewAudio");
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

  // Tarayıcı Canlı Konuşma Tanıma (Web Speech API)
  let browserSpeechRec = null;
  let liveBrowserTranscript = "";
  let isVoiceSearchActive = false;

  // Ses Önizleme Oynatıcısı
  let previewAudioEl = null;
  let isPreviewPlaying = false;

  // Karaoke & Söz Durumu
  const KARAOKE_LEAD_SEC = 1.1;
  let currentSong = null;
  let currentCandidates = [];
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

  // Canlı Telefon Mikrofonu ile Senkron Takibi
  let isLiveMicSyncing = false;
  let liveMicSyncTimerId = null;
  let liveSilenceCheckId = null;
  let autoSilencePaused = false;
  let lastSoundHeardPerfTime = 0;
  let isSyncAudioInFlight = false;

  // Sunucu durumu ve güvenli HTTPS tünel doğrulaması
  let serverStatus = { https_port: 8892, tunnel_url: "" };
  let verifiedTunnelUrl = "";

  async function apiFetch(path, options = {}) {
    return fetch(path, options);
  }

  async function verifyTunnelReachable(url) {
    if (!url || !url.startsWith("https://")) return false;
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 3000);
      const resp = await fetch(`${url.replace(/\/$/, "")}/api/status`, {
        signal: ctrl.signal,
        cache: "no-store",
      });
      clearTimeout(timer);
      if (resp.ok) {
        const d = await resp.json();
        return Boolean(d && d.ok);
      }
    } catch (_) {}
    return false;
  }

  async function checkServerAndHttps() {
    const isLocal =
      location.hostname === "localhost" ||
      location.hostname === "127.0.0.1";
    const isSecure = window.isSecureContext || location.protocol === "https:" || isLocal;

    async function fetchStatus() {
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        const data = await res.json();
        if (data && data.ok) {
          serverStatus = data;
          if (data.tunnel_url) {
            const alive = await verifyTunnelReachable(data.tunnel_url);
            if (alive) {
              verifiedTunnelUrl = data.tunnel_url;
              httpsRedirectLink.href = verifiedTunnelUrl;
              httpsRedirectLink.textContent = "🔒 Canlı Mikrofon İçin Aç (HTTPS)";
              httpsRedirectLink.classList.remove("hidden");
              return true;
            }
          }
          // Tünel henüz yoksa yerel HTTPS portunu yedek olarak bağla
          if (!isSecure && data.https_port) {
            httpsRedirectLink.href = `https://${location.hostname}:${data.https_port}`;
            httpsRedirectLink.textContent = `🔒 HTTPS Portunu Aç (:${data.https_port})`;
            httpsRedirectLink.classList.remove("hidden");
          }
        }
      } catch (_) {}
      return false;
    }

    const hasTunnel = await fetchStatus();

    if (!isSecure) {
      httpsBanner.classList.remove("hidden");
      if (!hasTunnel) {
        let attempts = 0;
        const interval = setInterval(async () => {
          attempts++;
          const found = await fetchStatus();
          if (found || attempts >= 6) clearInterval(interval);
        }, 3000);
      }
    }
  }

  // Katman Göstergelerini Güncelle
  function setLayerPills(wave) {
    stepLayer1.classList.toggle("active", wave >= 1);
    stepLayer2.classList.toggle("active", wave >= 1);
    stepLayer3.classList.toggle("active", wave >= 1);
    stepLayer4.classList.toggle("active", wave >= 1);
  }

  function resetLayerPills() {
    stepLayer1.classList.remove("active");
    stepLayer2.classList.remove("active");
    stepLayer3.classList.remove("active");
    stepLayer4.classList.remove("active");
  }

  // Ses Örneklerini Hedef Örnekleme Hızına (22050 Hz) İndirger (Hızlı yükleme + Tam Shazam spektrumu)
  function downsampleBuffer(buffer, inputRate, targetRate) {
    if (targetRate >= inputRate) {
      return { samples: buffer, rate: inputRate };
    }
    const ratio = inputRate / targetRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      let accum = 0;
      let count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
        accum += buffer[i];
        count++;
      }
      result[offsetResult] = count > 0 ? accum / count : 0;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return { samples: result, rate: targetRate };
  }

  // Float32 PCM dizisini 16-bit standart WAV Blob'una dönüştürür
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
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
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

  // Spektrogram tepe noktalarını bozmadan hafif doğrusal kazanç uygular
  function normalizeSamples(samples) {
    let maxPeak = 0;
    for (let i = 0; i < samples.length; i++) {
      const abs = Math.abs(samples[i]);
      if (abs > maxPeak) maxPeak = abs;
    }
    if (maxPeak <= 0.0005 || maxPeak >= 0.45) {
      return { samples, maxPeak };
    }
    // En fazla 6x doğrusal kazanç (gürültüyü patlatmadan sinyali netleştirir)
    const gain = Math.min(6.0, 0.75 / maxPeak);
    const out = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      out[i] = Math.max(-1, Math.min(1, samples[i] * gain));
    }
    return { samples: out, maxPeak };
  }

  // MediaRecorder'dan mevcut birikmiş ses parçalarını kesintisiz olarak alır
  function getMediaRecorderSnapshot() {
    if (!mediaRecorder) return null;
    try {
      if (mediaRecorder.state === "recording") {
        mediaRecorder.requestData();
      }
    } catch (_) {}
    if (recordedMediaChunks.length === 0) return null;
    const mime = mediaRecorder.mimeType || "audio/webm";
    return {
      blob: new Blob(recordedMediaChunks, { type: mime }),
      mime,
      ext: mime.includes("mp4") ? ".mp4" : ".webm",
    };
  }

  // Tarayıcı İçi Canlı Konuşma Tanıma (Web Speech API) Başlatıcı
  function startBrowserSpeechRecognition() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) return;
    stopBrowserSpeechRecognition();
    liveBrowserTranscript = "";

    try {
      browserSpeechRec = new SpeechRec();
      browserSpeechRec.lang = "tr-TR";
      browserSpeechRec.continuous = true;
      browserSpeechRec.interimResults = true;
      browserSpeechRec.maxAlternatives = 2;

      browserSpeechRec.onresult = (event) => {
        let interim = "";
        let finalStr = "";
        for (let i = 0; i < event.results.length; i++) {
          const res = event.results[i];
          if (res.isFinal) {
            finalStr += " " + res[0].transcript;
          } else {
            interim += " " + res[0].transcript;
          }
        }
        const combined = (finalStr + " " + interim).trim();
        if (combined.length >= 3) {
          liveBrowserTranscript = combined;
          detectedWordsBox.classList.remove("hidden");
          detectedWordsText.textContent = `"${combined}"`;
        }
      };

      browserSpeechRec.onerror = () => {};
      browserSpeechRec.start();
    } catch (_) {
      browserSpeechRec = null;
    }
  }

  function stopBrowserSpeechRecognition() {
    if (browserSpeechRec) {
      try {
        browserSpeechRec.stop();
      } catch (_) {}
      browserSpeechRec = null;
    }
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
      1: "Dalga 1/5: Shazam ses izi & canlı sözler taranıyor...",
      2: "Dalga 2/5: YouTube Music, LRCLIB & Shazam paralel taranıyor...",
      3: "Dalga 3/5: AI Vokal söz yakalama & şarkı eşleştirme...",
      4: "Dalga 4/5: DJ Hız/Pitch varyasyonları analiz ediliyor...",
      5: "Dalga 5/5: Derin katalog taraması yapılıyor...",
    };
    statusMessage.textContent = waveDescriptions[waveNum] || `Dalga ${waveNum} taranıyor...`;
    orbSubText.textContent = `Dalga ${waveNum} analiz ediliyor...`;

    let uploadBlob = null;
    let uploadExt = ".wav";
    let uploadMime = "audio/wav";

    // Öncelik 1: Kayıpsız kayan pencere PCM WAV (22050 Hz'e indirgenmiş, hızlı ve tam spektrumlu)
    const rawSamples = getRecentSamples(durationSec);
    if (rawSamples.length >= sampleRate * 1.0) {
      const { samples: normSamples, maxPeak } = normalizeSamples(rawSamples);
      if (maxPeak > 0.0002) {
        const { samples: downSamples, rate: downRate } = downsampleBuffer(normSamples, sampleRate, 22050);
        uploadBlob = encodeWAV(downSamples, downRate);
        uploadExt = ".wav";
        uploadMime = "audio/wav";
      }
    }

    // Öncelik 2: WebAudio sessizse donanım MediaRecorder yedeğini gönder
    if (!uploadBlob || uploadBlob.size < 1000) {
      const snap = getMediaRecorderSnapshot();
      if (snap && snap.blob && snap.blob.size > 800) {
        uploadBlob = snap.blob;
        uploadMime = snap.mime;
        uploadExt = snap.ext;
      } else if (rawSamples.length >= sampleRate * 0.5) {
        const { samples: downSamples, rate: downRate } = downsampleBuffer(rawSamples, sampleRate, 22050);
        uploadBlob = encodeWAV(downSamples, downRate);
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
      const headers = {
        "Content-Type": uploadMime,
        "X-Wave": String(waveNum),
        "X-File-Ext": uploadExt,
        "X-Lang": "auto",
      };
      if (liveBrowserTranscript) {
        headers["X-Browser-Transcript"] = encodeURIComponent(liveBrowserTranscript);
      }

      const resp = await apiFetch("/api/recognize", {
        method: "POST",
        headers,
        body: uploadBlob,
        signal: controller.signal,
      });

      const data = await resp.json();
      if (!isListening) return;

      if (data && data.ok && data.result) {
        const res = data.result;

        if (res.detected_words && res.detected_words.length > 0) {
          detectedWordsBox.classList.remove("hidden");
          detectedWordsText.textContent = `"${res.detected_words.join(" / ")}"`;
        } else if (res.detected_vocal_phrase) {
          detectedWordsBox.classList.remove("hidden");
          detectedWordsText.textContent = `"${res.detected_vocal_phrase}"`;
        }

        if (res.recognized && res.title) {
          const latencySec = (performance.now() - waveCapturedAt) / 1000.0;
          if (typeof res.offset_seconds === "number") {
            res.offset_seconds = Math.max(0, res.offset_seconds + latencySec);
          }
          if (!res.lines || res.lines.length === 0) {
            const enriched = await clientSideLyricsFallback(`${res.artist || ""} ${res.title || ""}`.trim(), res);
            if (enriched && enriched.lines && enriched.lines.length > 0) {
              res.lines = enriched.lines;
              res.plain_lyrics = enriched.plain_lyrics;
              res.synced = enriched.synced;
              if (!res.cover && enriched.cover) res.cover = enriched.cover;
              if (!res.preview_url && enriched.preview_url) res.preview_url = enriched.preview_url;
            }
          }
          stopListening(true);
          if (navigator.vibrate) {
            navigator.vibrate([80, 40, 130]);
          }
          renderRecognizedSong(res, res.candidates || []);
          return;
        }

        // Eğer sunucu ses izinden bulamadıysa ama mikrofondan net söz kelimeleri duyulduysa doğrudan sözlerden eşleştir
        const heardPhrase =
          (res.detected_words && res.detected_words[0]) ||
          res.detected_vocal_phrase ||
          (waveNum >= 2 ? liveBrowserTranscript : "");
        if (heardPhrase && heardPhrase.trim().split(/\s+/).length >= 3) {
          const vocalMatch = await clientSideLyricsFallback(heardPhrase.trim());
          if (vocalMatch && vocalMatch.lines && vocalMatch.lines.length > 0) {
            vocalMatch.layer_used = "Katman 3: Canlı Vokal Söz Eşleştirme";
            stopListening(true);
            if (navigator.vibrate) {
              navigator.vibrate([80, 40, 130]);
            }
            renderRecognizedSong(vocalMatch, vocalMatch.candidates || []);
            return;
          }
        }
      }

      if (waveNum >= 5 && isListening) {
        // Eğer tarayıcı canlı söz yakaladıysa son olarak doğrudan metin araması yap
        if (liveBrowserTranscript && liveBrowserTranscript.length >= 4) {
          const fallbackQuery = liveBrowserTranscript;
          stopListening(false);
          await performTextSearch(fallbackQuery);
          return;
        }
        stopListening(false);
        statusMessage.textContent =
          "Bu kesitte eşleşme bulunamadı. Müziğe yaklaştırıp tekrar dokunun veya duyduğunuz birkaç kelimeyi aşağıya yazın.";
      }
    } catch (err) {
      if (err && err.name === "AbortError") return;
      console.error("Dalga gönderim hatası:", err);
    }
  }

  // Tek Tuşla Bulana Kadar Dinlemeyi Başlat
  async function startListening() {
    stopPreviewAudio();

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      if (verifiedTunnelUrl) {
        statusMessage.textContent = "🔒 Canlı mikrofon için doğrulanmış güvenli HTTPS adresine geçiliyor...";
        location.href = verifiedTunnelUrl;
        return;
      }
      statusMessage.textContent = "🎙️ Telefonun mikrofon kaydedicisi açılıyor, 5-6 saniye müziği kaydedip onaylayın...";
      nativeAudioInput.click();
      return;
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
          "Mikrofon izni alınamadı. Aşağıdaki 'Telefonun Ses Kaydedicisiyle Bul' butonunu veya arama çubuğunu kullanabilirsiniz.";
        return;
      }
    }

    isListening = true;
    pcmChunks = [];
    recordedMediaChunks = [];
    currentAbortControllers = [];
    liveBrowserTranscript = "";
    detectedWordsBox.classList.add("hidden");
    detectedWordsText.textContent = "";

    // Yedek MediaRecorder (Kesintisiz çalışır)
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
        mediaRecorder.start(500);
      } catch (e) {
        console.warn("MediaRecorder başlatılamadı:", e);
      }
    }

    // Tarayıcı içi canlı söz yakalamayı başlat
    startBrowserSpeechRecognition();

    btnListen.classList.add("listening");
    orbIcon.textContent = "📡";
    orbMainText.textContent = "DİNLENİYOR...";
    orbSubText.textContent = "Durdurmak için dokunun";
    statusMessage.textContent = "Müzik ve vokal taranıyor (Shazam + YouTube Music + AI)...";
    setLayerPills(1);

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
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
      if (rms > 0.002) {
        lastSoundHeardPerfTime = performance.now();
      }
      pcmChunks.push(new Float32Array(input));
      const maxChunks = Math.ceil((22 * sampleRate) / 4096);
      if (pcmChunks.length > maxChunks) {
        pcmChunks.shift();
      }
    };

    // Mobil tarayıcıların ses grafiğini uyutmaması için çok küçük kazanç
    const silentGain = audioCtx.createGain();
    silentGain.gain.value = 0.00001;

    sourceNode.connect(analyserNode);
    analyserNode.connect(processorNode);
    processorNode.connect(silentGain);
    silentGain.connect(audioCtx.destination);
    lastSoundHeardPerfTime = performance.now();

    drawVisualizer();

    waveTimers = [
      setTimeout(() => sendAudioWave(1, 3.8), 3200),
      setTimeout(() => sendAudioWave(2, 5.8), 6000),
      setTimeout(() => sendAudioWave(3, 8.0), 9200),
      setTimeout(() => sendAudioWave(4, 10.0), 13000),
      setTimeout(() => sendAudioWave(5, 11.5), 17000),
    ];
  }

  function closeMicStream() {
    isLiveMicSyncing = false;
    stopBrowserSpeechRecognition();
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
    stopBrowserSpeechRecognition();

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
    orbSubText.textContent = "Müziği veya söylenen sözü bulur";
  }

  function formatTime(sec) {
    if (typeof sec !== "number" || isNaN(sec) || sec < 0) return "00:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // Ses Önizleme (Preview) Kontrolü
  function stopPreviewAudio() {
    if (previewAudioEl) {
      try {
        previewAudioEl.pause();
        previewAudioEl.currentTime = 0;
      } catch (_) {}
      previewAudioEl = null;
    }
    isPreviewPlaying = false;
    if (btnPreviewAudio) {
      btnPreviewAudio.textContent = "▶️ Müziği Çal";
      btnPreviewAudio.classList.remove("active");
    }
  }

  function togglePreviewAudio() {
    if (!currentSong || !currentSong.preview_url) return;
    if (isPreviewPlaying && previewAudioEl) {
      stopPreviewAudio();
      return;
    }
    stopPreviewAudio();
    try {
      previewAudioEl = new Audio(currentSong.preview_url);
      previewAudioEl.onended = () => {
        stopPreviewAudio();
      };
      previewAudioEl.play().then(() => {
        isPreviewPlaying = true;
        btnPreviewAudio.textContent = "⏹️ Müziği Durdur";
        btnPreviewAudio.classList.add("active");
      }).catch(() => {
        stopPreviewAudio();
      });
    } catch (_) {
      stopPreviewAudio();
    }
  }

  if (btnPreviewAudio) {
    btnPreviewAudio.addEventListener("click", togglePreviewAudio);
  }

  // Alternatif Eşleşen Şarkıları Göster
  function renderCandidatesBar(candidates) {
    currentCandidates = Array.isArray(candidates) ? candidates : [];
    if (!candidatesBar || !candidatesList) return;

    if (currentCandidates.length <= 1) {
      candidatesBar.classList.add("hidden");
      candidatesList.innerHTML = "";
      return;
    }

    candidatesBar.classList.remove("hidden");
    candidatesList.innerHTML = "";

    currentCandidates.forEach((cand) => {
      const chip = document.createElement("button");
      chip.type = "button";
      const isCurrent =
        currentSong &&
        (cand.title || "").toLowerCase() === (currentSong.title || "").toLowerCase() &&
        (cand.artist || "").toLowerCase() === (currentSong.artist || "").toLowerCase();
      chip.className = `candidate-chip${isCurrent ? " active" : ""}`;
      chip.textContent = `${cand.title}${cand.artist ? " — " + cand.artist : ""}`;
      chip.addEventListener("click", async () => {
        if (isCurrent) return;
        chip.textContent = "⏳ Yükleniyor...";
        try {
          const qs = new URLSearchParams({
            title: cand.title || "",
            artist: cand.artist || "",
            cover: cand.cover || "",
          });
          const resp = await apiFetch(`/api/lyrics?${qs.toString()}`);
          if (resp.ok) {
            const data = await resp.json();
            if (data && data.ok && data.result && data.result.lines && data.result.lines.length > 0) {
              renderRecognizedSong(data.result, currentCandidates);
              return;
            }
          }
          // Yedek: /api/search veya doğrudan istemci LRCLIB araması
          const fallbackSong = await clientSideLyricsFallback(`${cand.artist || ""} ${cand.title || ""}`.trim(), cand);
          if (fallbackSong) {
            renderRecognizedSong(fallbackSong, currentCandidates);
          }
        } catch (_) {}
      });
      candidatesList.appendChild(chip);
    });
  }

  // Bulunan Şarkıyı ve Sözlerini Sahne Ekranında Göster
  function renderRecognizedSong(song, candidates = null) {
    stopPreviewAudio();
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

    if (btnPreviewAudio) {
      if (song.preview_url) {
        btnPreviewAudio.classList.remove("hidden");
        btnPreviewAudio.textContent = "▶️ Müziği Çal";
      } else {
        btnPreviewAudio.classList.add("hidden");
      }
    }

    if (song.synced) {
      badgeSync.textContent = mediaStream ? "🎙️ Telefon Sesiyle Canlı Senkron" : "⚡ Canlı Senkronize Karaoke";
      badgeSync.classList.remove("hidden");
      syncToolbar.classList.remove("hidden");
    } else {
      badgeSync.textContent = mediaStream ? "🎙️ Telefon Sesiyle Söz Takibi" : "📜 Tam Şarkı Sözleri";
      badgeSync.classList.remove("hidden");
      syncToolbar.classList.add("hidden");
    }

    renderCandidatesBar(candidates !== null ? candidates : (song.candidates || []));
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
        "Şarkı bulundu ancak bu parçanın metin sözleri veritabanında yer almıyor. Üstteki diğer eşleşmeleri deneyebilirsiniz.";
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
      let targetScroll = activeEl.offsetTop - containerHeight * 0.16;

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

  async function pollLiveAudioSync() {
    if (!isLiveMicSyncing || !currentSong || syncPaused || isSongFinished || isSyncAudioInFlight) {
      return;
    }
    const rawSamples = getRecentSamples(4.2);
    if (rawSamples.length < sampleRate * 2.5) return;

    const { samples: normSamples, maxPeak } = normalizeSamples(rawSamples);
    if (maxPeak <= 0.002) {
      return;
    }

    isSyncAudioInFlight = true;
    const capturePerf = performance.now();
    const currentOffsetAtCapture = getCurrentPlaybackSec();
    const { samples: downSamples, rate: downRate } = downsampleBuffer(normSamples, sampleRate, 22050);
    const wavBlob = encodeWAV(downSamples, downRate);

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

          if (sr.method === "acoustic_live") {
            if (drift > 1.25 && drift < 25.0) {
              syncBaseOffsetSec = currentSec + drift * 0.65;
              syncStartPerfTime = performance.now();
              updateSyncedHighlight(false);
            } else if (drift < -1.8 && drift > -6.0) {
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
    } finally {
      isSyncAudioInFlight = false;
    }
  }

  function startLiveMicSync() {
    if (!mediaStream || !audioCtx) return;
    isLiveMicSyncing = true;
    lastSoundHeardPerfTime = performance.now();

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

    liveMicSyncTimerId = setInterval(pollLiveAudioSync, 4200);
  }

  function updateSyncedHighlight(allowBackward = false) {
    if (!currentSong || !currentSong.synced || !currentSong.lines) return;
    const nowSec = getCurrentPlaybackSec();
    syncTimerText.textContent = formatTime(nowSec);

    const lines = currentSong.lines;
    const lastLine = lines[lines.length - 1];
    const lastLineTime = (lastLine && typeof lastLine.time === "number") ? lastLine.time : (currentSong.duration || 240);

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
    for (let i = 0; i < lines.length; i++) {
      const t = lines[i].time;
      if (typeof t === "number" && t <= nowSec + KARAOKE_LEAD_SEC) {
        foundIdx = i;
      } else if (typeof t === "number" && t > nowSec + KARAOKE_LEAD_SEC) {
        break;
      }
    }

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
      updateSyncedHighlight(true);
      syncIntervalId = setInterval(() => updateSyncedHighlight(false), 180);
    } else {
      highlightLineByIndex(initialIdx, false);
      plainScrollIntervalId = setInterval(() => {
        if (!autoScrollEnabled || syncPaused || autoSilencePaused || isSongFinished) return;
        const nextIdx = activeLineIdx + 1;
        if (currentSong && currentSong.lines) {
          if (nextIdx < currentSong.lines.length) {
            highlightLineByIndex(nextIdx, true);
          } else {
            isSongFinished = true;
            stopKaraokeSync(true);
            if (btnStopSync) btnStopSync.textContent = "🔄 Baştan";
            btnSyncPause.textContent = "🔄 Baştan";
          }
        }
      }, 4500);
    }

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

  function adjustSync(deltaSec) {
    syncBaseOffsetSec = Math.max(0, getCurrentPlaybackSec() + deltaSec);
    syncStartPerfTime = performance.now();
    updateSyncedHighlight(true);
  }

  if (btnSyncBack1) btnSyncBack1.addEventListener("click", () => adjustSync(-1.0));
  if (btnSyncBack05) btnSyncBack05.addEventListener("click", () => adjustSync(-0.5));
  if (btnSyncForward05) btnSyncForward05.addEventListener("click", () => adjustSync(0.5));
  if (btnSyncForward1) btnSyncForward1.addEventListener("click", () => adjustSync(1.0));

  function closeLyricsView() {
    stopPreviewAudio();
    stopKaraokeSync(true);
    currentSong = null;
    document.body.classList.remove("lyrics-active");
    lyricsSection.classList.add("hidden");
    listenSection.classList.remove("hidden");
    statusMessage.textContent = "Hazır. Müziği dinletmek için dokunun veya arama kutusuna yazın.";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  if (btnCloseLyrics) {
    btnCloseLyrics.addEventListener("click", closeLyricsView);
  }

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

  btnListenAgain.addEventListener("click", () => {
    closeLyricsView();
    startListening();
  });

  btnListen.addEventListener("click", () => {
    if (isListening) {
      stopListening(false);
      statusMessage.textContent = "Dinleme durduruldu. Tekrar dinlemek için dokunun.";
    } else {
      startListening();
    }
  });

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
        renderRecognizedSong(data.result, data.result.candidates || []);
      } else {
        statusMessage.textContent =
          "Kayıttan şarkı çıkarılamadı. Müziğe daha yakın tutup 5-6 saniye kaydedin veya söz arayın.";
      }
    } catch (_) {
      resetLayerPills();
      statusMessage.textContent = "Sunucuya gönderilirken hata oluştu.";
    }
    nativeAudioInput.value = "";
  });

  // İstemci Tabanlı Doğrudan LRCLIB + iTunes Yedek Arama Motoru (Sunucu yanıt vermezse bile %100 çalışır)
  function parseLrcClient(lrcText) {
    if (!lrcText) return [];
    const out = [];
    const re = /\[(\d{1,2}):(\d{2})(?:[\.:](\d{1,3}))?\](.*)/;
    const rawLines = lrcText.split(/\r?\n/);
    for (let i = 0; i < rawLines.length; i++) {
      const m = re.exec(rawLines[i].trim());
      if (!m) continue;
      const mins = parseInt(m[1], 10);
      const secs = parseInt(m[2], 10);
      const fracStr = m[3] || "0";
      const frac =
        parseInt(fracStr, 10) /
        (fracStr.length === 3 ? 1000 : fracStr.length === 2 ? 100 : 10);
      const text = (m[4] || "").trim();
      if (text && text !== "♪") {
        out.push({ time: +(mins * 60 + secs + frac).toFixed(2), text });
      }
    }
    out.sort((a, b) => a.time - b.time);
    out.forEach((item, idx) => {
      item.index = idx;
    });
    return out;
  }

  async function clientSideLyricsFallback(query, preferredMeta = null) {
    try {
      const [lrcResp, itunesResp] = await Promise.allSettled([
        fetch(`https://lrclib.net/api/search?q=${encodeURIComponent(query)}`),
        fetch(`https://itunes.apple.com/search?term=${encodeURIComponent(query)}&country=TR&entity=song&limit=5`),
      ]);

      let lrcItems = [];
      if (lrcResp.status === "fulfilled" && lrcResp.value.ok) {
        const d = await lrcResp.value.json();
        if (Array.isArray(d)) lrcItems = d;
      }

      let itunesItems = [];
      if (itunesResp.status === "fulfilled" && itunesResp.value.ok) {
        const d = await itunesResp.value.json();
        if (d && Array.isArray(d.results)) itunesItems = d.results;
      }

      const bestItunes = itunesItems[0] || {};
      const coverUrl =
        (preferredMeta && preferredMeta.cover) ||
        (bestItunes.artworkUrl100 ? bestItunes.artworkUrl100.replace("100x100bb", "500x500bb") : "");
      const previewUrl = bestItunes.previewUrl || "";

      const validLrc = lrcItems.filter((x) => x && (x.syncedLyrics || x.plainLyrics));
      if (validLrc.length > 0) {
        const syncedItem = validLrc.find((x) => x.syncedLyrics) || validLrc[0];
        const lines = syncedItem.syncedLyrics
          ? parseLrcClient(syncedItem.syncedLyrics)
          : (syncedItem.plainLyrics || "")
              .split(/\r?\n/)
              .map((t) => t.trim())
              .filter(Boolean)
              .map((t, i) => ({ index: i, time: null, text: t }));

        const candidates = validLrc.slice(0, 5).map((item) => ({
          title: item.trackName || "",
          artist: item.artistName || "",
          album: item.albumName || "",
          cover: coverUrl,
          synced: Boolean(item.syncedLyrics),
          source: "LRCLIB",
        }));

        return {
          recognized: true,
          title: syncedItem.trackName || (preferredMeta && preferredMeta.title) || bestItunes.trackName || query,
          artist: syncedItem.artistName || (preferredMeta && preferredMeta.artist) || bestItunes.artistName || "",
          album: syncedItem.albumName || bestItunes.collectionName || "",
          cover: coverUrl,
          preview_url: previewUrl,
          duration: syncedItem.duration || null,
          synced: Boolean(syncedItem.syncedLyrics && lines.length > 0),
          lines,
          plain_lyrics: syncedItem.plainLyrics || lines.map((x) => x.text).join("\n"),
          source: syncedItem.syncedLyrics ? "LRCLIB (Senkronize Karaoke)" : "LRCLIB",
          layer_used: "Doğrudan Söz / Şarkı Arama",
          matched_line_index: 0,
          offset_seconds: 0,
          candidates,
        };
      }
    } catch (_) {}
    return null;
  }

  // Hızlı Söz / İsim Arama Fonksiyonu
  async function performTextSearch(q) {
    const cleaned = (q || "").trim();
    if (!cleaned) return;

    statusMessage.textContent = `"${cleaned}" için şarkı ve senkronize sözler aranıyor...`;
    try {
      const resp = await apiFetch(`/api/search?q=${encodeURIComponent(cleaned)}`);
      const data = await resp.json();
      if (data && data.ok && data.result && data.result.lines && data.result.lines.length > 0) {
        searchInput.value = "";
        let cands = data.candidates || data.result.candidates || [];
        if (cands.length === 0) {
          const fb = await clientSideLyricsFallback(cleaned);
          if (fb && fb.candidates) cands = fb.candidates;
          if (fb && fb.preview_url && !data.result.preview_url) {
            data.result.preview_url = fb.preview_url;
          }
        }
        renderRecognizedSong(data.result, cands);
        return;
      }
      // Sunucu söz bulamadıysa doğrudan istemci LRCLIB + iTunes motorunu çalıştır
      const fallback = await clientSideLyricsFallback(cleaned, data && data.result ? data.result : null);
      if (fallback) {
        searchInput.value = "";
        renderRecognizedSong(fallback, fallback.candidates || []);
        return;
      }
      statusMessage.textContent =
        (data && data.error) || "Eşleşen şarkı bulunamadı. Farklı birkaç kelime deneyin.";
    } catch (_) {
      const fallback = await clientSideLyricsFallback(cleaned);
      if (fallback) {
        searchInput.value = "";
        renderRecognizedSong(fallback, fallback.candidates || []);
        return;
      }
      statusMessage.textContent = "Arama sırasında bağlantı hatası oluştu.";
    }
  }

  searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await performTextSearch(searchInput.value);
  });

  // Sesli Söz / Şarkı Arama Butonu (🎤)
  if (btnVoiceSearch) {
    btnVoiceSearch.addEventListener("click", () => {
      const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SpeechRec) {
        statusMessage.textContent = "Tarayıcınız doğrudan sesli yazmayı desteklemiyor; üstteki DOKUN & DİNLET küresine basın.";
        return;
      }
      if (isVoiceSearchActive) {
        isVoiceSearchActive = false;
        btnVoiceSearch.classList.remove("recording");
        return;
      }
      try {
        const rec = new SpeechRec();
        rec.lang = "tr-TR";
        rec.continuous = false;
        rec.interimResults = true;
        isVoiceSearchActive = true;
        btnVoiceSearch.classList.add("recording");
        statusMessage.textContent = "🎤 Şarkı adını veya sözlerini söyleyin...";

        rec.onresult = (event) => {
          let text = "";
          for (let i = 0; i < event.results.length; i++) {
            text += event.results[i][0].transcript;
          }
          searchInput.value = text.trim();
          if (event.results[event.results.length - 1].isFinal && text.trim().length >= 2) {
            isVoiceSearchActive = false;
            btnVoiceSearch.classList.remove("recording");
            performTextSearch(text.trim());
          }
        };
        rec.onerror = () => {
          isVoiceSearchActive = false;
          btnVoiceSearch.classList.remove("recording");
        };
        rec.onend = () => {
          isVoiceSearchActive = false;
          btnVoiceSearch.classList.remove("recording");
        };
        rec.start();
      } catch (_) {
        isVoiceSearchActive = false;
        btnVoiceSearch.classList.remove("recording");
      }
    });
  }

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
          renderRecognizedSong(item, []);
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

  checkServerAndHttps();
})();
