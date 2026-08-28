(function(){
'use strict';

var TIPO = document.documentElement.getAttribute('data-tipo') || 'transcripcion';
var LIMITE = 50;

function apiBase(){
  var p = location.pathname;
  if (p.indexOf('/keypoints/') !== -1) return '../../api/';
  if (p.indexOf('/panel/') !== -1) return '../api/';
  return 'api/';
}
function apiUrl(pathWithQuery){
  return apiBase() + pathWithQuery.replace(/^\/+/, '');
}

var DATOS = [];
var mapa = null;
var marcador = null;
var carruselInterval = null;
var kpActualIdx = -1;

function escHtml(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function truncar(texto,n){
  if(!texto) return '';
  if(texto.length<=n) return texto;
  return texto.slice(0,n)+'…';
}

function initMapa(){
  mapa = L.map('mapa').setView([-26.8, -65.2], 6);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Esri World Light Gray',
    maxZoom: 16
  }).addTo(mapa);
}

function renderLista(){
  var lista = document.getElementById('lista');
  if (!DATOS.length){
    lista.innerHTML = '<div style="padding:12px;color:#777;">Sin keypoints para mostrar.</div>';
    return;
  }
  lista.innerHTML = '';
  DATOS.forEach(function(d, idx){
    var card = document.createElement('div');
    card.className = 'card' + (idx===0 ? ' activa' : '');
    card.dataset.idx = idx;
    var badgeSrc = d.posicion_fuente ? '<span class="badge '+d.posicion_fuente+'">'+d.posicion_fuente+'</span>' : '<span class="badge sin">sin posición</span>';
    var hora = d.timestamp_absolute ? d.timestamp_absolute.slice(11,19) : (d.hora || '');
    var fecha = d.timestamp_absolute ? d.timestamp_absolute.slice(0,10) : (d.fecha || '');
    var tipoMedia = d.media_tipo || '?';
    card.innerHTML =
      '<div><span class="badge">'+escHtml(d.kp_key)+'</span>'+badgeSrc+'</div>'+
      '<div class="valor" title="'+escHtml(d.value||'')+'">'+escHtml(truncar(d.value||'',90))+'</div>'+
      '<div class="meta">'+escHtml(fecha)+' '+escHtml(hora)+' · offset '+(d.offset_secs!=null ? Number(d.offset_secs).toFixed(1)+'s' : '—')+' · media #'+d.media_id+' ['+escHtml(tipoMedia)+']</div>';
    card.addEventListener('click', function(){ mostrarDetalle(idx); });
    lista.appendChild(card);
  });
}

function actualizarSubtitulo(total){
  var sub = document.getElementById('subtitulo');
  if (!sub) return;
  var tipoLabel = TIPO === 'contexto' ? 'contexto' : 'transcripción';
  sub.textContent = total + ' keypoints al azar · tipo '+tipoLabel+' · límite '+LIMITE+' · fetch '+apiUrl('keypoints.php?tipo='+encodeURIComponent(TIPO)+'&limite='+LIMITE);
}

function mostrarEstado(msg, isError){
  var lista = document.getElementById('lista');
  lista.innerHTML = '<div class="estado '+(isError?'error':'')+'">'+escHtml(msg)+'</div>';
  var detalle = document.getElementById('detalle-contenido');
  if (detalle) detalle.style.display = 'none';
}

function cargarKeypoints(){
  var btn = document.getElementById('btn-mezclar');
  if (btn) { btn.disabled = true; btn.textContent = 'Cargando…'; }
  mostrarEstado('Cargando '+LIMITE+' keypoints al azar…', false);
  var url = apiUrl('keypoints.php?tipo='+encodeURIComponent(TIPO)+'&limite='+LIMITE);
  fetch(url)
    .then(function(r){
      if (!r.ok) throw new Error('HTTP '+r.status);
      return r.json();
    })
    .then(function(data){
      DATOS = data.keypoints || [];
      actualizarSubtitulo(DATOS.length);
      var cont = document.getElementById('detalle-contenido');
      if (cont) cont.style.display = DATOS.length ? 'block' : 'none';
      if (!DATOS.length){
        mostrarEstado('No se encontraron keypoints del tipo "'+TIPO+'" (total_disponible='+ (data.total_disponible||0) +').', false);
        return;
      }
      renderLista();
      mostrarDetalle(0);
    })
    .catch(function(e){
      mostrarEstado('Error cargando keypoints: '+e.message+' — URL: '+url, true);
    })
    .finally(function(){
      if (btn) { btn.disabled = false; btn.textContent = 'Mezclar 50 al azar'; }
    });
}

function mostrarDetalle(idx){
  document.querySelectorAll('.card').forEach(function(c){ c.classList.remove('activa'); });
  var card = document.querySelector('.card[data-idx="'+idx+'"]');
  if (card) card.classList.add('activa');
  kpActualIdx = idx;
  var d = DATOS[idx];
  // Player
  var wrap = document.getElementById('player-wrap');
  wrap.innerHTML = '';
  var mediaUrl = apiUrl('servir_medio.php?id='+encodeURIComponent(d.media_id));
  // Transcripción: arrancar 5 s antes del keypoint si es posible (contexto auditivo)
  var seekOffset = null;
  if (d.offset_secs != null) {
    var raw = Number(d.offset_secs);
    seekOffset = (TIPO === 'transcripcion') ? Math.max(0, raw - 5) : raw;
  }
  if (d.media_tipo === 'video'){
    var v = document.createElement('video');
    v.controls = true; v.id='player'; v.src = mediaUrl;
    if (seekOffset != null){
      v.addEventListener('loadedmetadata', function(){ try{ v.currentTime = seekOffset; }catch(e){} }, {once:true});
    }
    wrap.appendChild(v);
    v.addEventListener('error', function(){
      var aviso = document.createElement('div');
      aviso.style.cssText='padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:13px;margin-top:6px;';
      aviso.textContent='No se pudo cargar el medio #'+d.media_id+' ('+d.archivo+')';
      wrap.appendChild(aviso);
    });
  } else if (d.media_tipo === 'audio'){
    var a = document.createElement('audio');
    a.controls = true; a.id='player'; a.style.background='#fff'; a.style.border='1px solid #ddd'; a.src = mediaUrl;
    if (seekOffset != null){
      a.addEventListener('loadedmetadata', function(){ try{ a.currentTime = seekOffset; }catch(e){} }, {once:true});
    }
    wrap.appendChild(a);
    a.addEventListener('error', function(){
      var aviso = document.createElement('div');
      aviso.style.cssText='padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:13px;margin-top:6px;';
      aviso.textContent='No se pudo cargar el audio #'+d.media_id+' ('+d.archivo+')';
      wrap.appendChild(aviso);
    });
  } else {
    wrap.innerHTML = '<div style="padding:8px;background:#fff3cd;border:1px solid #ffc107;border-radius:6px;font-size:13px;">Tipo '+escHtml(d.media_tipo||'?')+' — sin player (archivo: '+escHtml(d.archivo||'')+')</div>';
  }

  // Meta
  var meta = document.getElementById('detalle-meta');
  var posTxt = (d.latitud!=null && d.longitud!=null) ? (Number(d.latitud).toFixed(6)+', '+Number(d.longitud).toFixed(6)+' ('+(d.posicion_fuente||'')+')') : 'sin posición GPS';
  meta.innerHTML = '<strong>key:</strong> <code>'+escHtml(d.kp_key)+'</code> · <strong>pos:</strong> '+escHtml(posTxt)+' · <strong>media:</strong> #'+d.media_id+' · <strong>offset:</strong> '+(d.offset_secs!=null?Number(d.offset_secs).toFixed(1)+'s':'—')+' · <strong>ts:</strong> '+escHtml(d.timestamp_absolute||'')+'<br><strong>archivo:</strong> <code>'+escHtml(d.archivo||'')+'</code> en <code>'+escHtml(d.carpeta||'')+'</code>';

  document.getElementById('detalle-texto').textContent = d.value || '';

  // Mapa
  if (mapa){
    var avisoPrevio = document.querySelector('#mapa .sin-posicion');
    if (avisoPrevio) avisoPrevio.remove();
    if (d.latitud!=null && d.longitud!=null){
      mapa.setView([Number(d.latitud), Number(d.longitud)], 12);
      if (marcador) mapa.removeLayer(marcador);
      marcador = L.marker([Number(d.latitud), Number(d.longitud)]).addTo(mapa);
      setTimeout(function(){ mapa.invalidateSize(); }, 100);
    } else {
      mapa.setView([-26.8,-65.2], 6);
      if (marcador){ mapa.removeLayer(marcador); marcador=null; }
      var aviso = document.createElement('div');
      aviso.className='sin-posicion';
      aviso.textContent='Sin posición GPS para este keypoint';
      document.getElementById('mapa').appendChild(aviso);
      setTimeout(function(){ mapa.invalidateSize(); }, 100);
    }
  }

  // Carrusel lazy
  cargarFotosCercanas(d);
}

function cargarFotosCercanas(d){
  var carrusel = document.getElementById('carrusel');
  if (carruselInterval){ clearInterval(carruselInterval); carruselInterval=null; }
  carrusel.innerHTML = '<div class="vacio">Cargando fotos cercanas…</div>';
  carrusel.style.height='360px';
  var url = apiUrl('fotos_cercanas.php?kp_id='+encodeURIComponent(d.id));
  fetch(url)
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); })
    .then(function(data){
      var fotos = data.fotos || [];
      if (!fotos.length){
        carrusel.innerHTML = '<div class="vacio">Sin imágenes cercanas en el pool.</div>';
        carrusel.style.height='auto';
        return;
      }
      carrusel.innerHTML='';
      carrusel.style.height='360px';
      var idxFoto=0;
      var imgs = fotos.map(function(f,i){
        var img = document.createElement('img');
        img.src = apiUrl('servir_medio.php?id='+encodeURIComponent(f.id)+'&thumb=1');
        var distTxt = f.dist_m!=null ? ' — '+f.dist_m+' m' : (f.delta_secs!=null ? ' — Δ '+f.delta_secs+'s' : '');
        img.title = (f.archivo||('foto #'+f.id)) + distTxt + (f.fecha?' — '+f.fecha+(f.hora?' '+f.hora:''):'');
        img.alt = f.archivo||String(f.id);
        img.loading='lazy';
        if(i===0) img.classList.add('activa');
        img.addEventListener('error', function(){ img.style.opacity='0.2'; });
        carrusel.appendChild(img);
        return img;
      });
      var contador = document.createElement('div');
      contador.className='contador';
      carrusel.appendChild(contador);
      function actualizarContador(){
        contador.textContent = (idxFoto+1)+' / '+imgs.length+' — '+imgs[idxFoto].title;
      }
      actualizarContador();
      if (imgs.length>1){
        carruselInterval = setInterval(function(){
          imgs[idxFoto].classList.remove('activa');
          idxFoto = (idxFoto+1)%imgs.length;
          imgs[idxFoto].classList.add('activa');
          actualizarContador();
        }, 3000);
      }
    })
    .catch(function(e){
      carrusel.innerHTML = '<div class="vacio">Error cargando fotos: '+escHtml(e.message)+'</div>';
    });
}

document.addEventListener('DOMContentLoaded', function(){
  initMapa();
  var btn = document.getElementById('btn-mezclar');
  if (btn) btn.addEventListener('click', function(){ cargarKeypoints(); });
  cargarKeypoints();
});

})();
