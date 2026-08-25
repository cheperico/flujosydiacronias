(function() {
    'use strict';

    // ═══════════════════════════════════════════════════════
    //  DATOS — colores, horas, provincias, municipios
    // ═══════════════════════════════════════════════════════

    var COLORES = [
        { nombre: 'azul',     hex: '#1976d2' },
        { nombre: 'negro',    hex: '#212121' },
        { nombre: 'gris',     hex: '#757575' },
        { nombre: 'verde',    hex: '#388e3c' },
        { nombre: 'marr\u00f3n', hex: '#5d4037' },
        { nombre: 'amarillo', hex: '#fbc02d' },
        { nombre: 'rojo',     hex: '#d32f2f' },
        { nombre: 'rosa',     hex: '#e91e63' },
        { nombre: 'violeta',  hex: '#7b1fa2' },
        { nombre: 'blanco',   hex: '#f5f5f5' },
        { nombre: 'naranja',  hex: '#f57c00' }
    ];
    var coloresSeleccionados = [];

    var HORAS = [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23];
    var horasSeleccionadas = [];
    var horaActual = 12;   // última hora clickeada → maneja la paleta

    var PROVINCIAS = [
        { nombre: 'CABA' },
        { nombre: 'Buenos Aires' },
        { nombre: 'C\u00f3rdoba' }
    ];
    var provinciasSeleccionadas = [];

    var MUNICIPIOS = [
        'Luj\u00e1n', 'Bell Ville', 'C\u00f3rdoba', 'Rojas',
        'Villa Mar\u00eda', 'Oncativo', 'Carmen de Areco',
        'Ballesteros', 'Salto', 'Saladillo'
    ];
    var municipiosSeleccionados = [];
    // Mapa municipio -> provincia (cada municipio pertenece a UNA provincia).
    var MUNICIPIOS_POR_PROVINCIA = {};

    // ═══════════════════════════════════════════════════════
    //  FLOW — duración y ciclo de paleta
    // ═══════════════════════════════════════════════════════

    var DATOS_CARGADOS = false;
    var DATOS_TOTAL = 0;
    var DATOS_API = null;
    var TAGS_API = null;
    var tagsSeleccionados = [];
    var MEDIOS_FILTRADOS = null;
    var MEDIOS_360 = null;
    var MEDIOS_REQUEST_ID = 0;
    var MENSAJES_TELEGRAM = null;
    var MENSAJES_TELEGRAM_MUNICIPIO = '';

    // ── SONIDO: botón interruptor + motor de reproducción de audios ──
    // La batería de audios se distribuye pareja a lo largo del fluir:
    // 10s de silencio al inicio y al final, y silencio entre audios.
    var SONIDO = {
        habilitado: false,    // interruptor general (botón lateral) — arranca apagado
        items: [],            // audios seleccionados (con duracion_seg)
        plan: [],             // plan de reproducción [{audio, inicioMs, duracionMs}]
        reproduciendo: false,
        elem: null            // elemento <audio> actual
    };
    // Constantes del motor (en ms)
    var SONIDO_SILENCIO_INICIO = 10000;   // 10s mudo al arrancar el fluir
    var SONIDO_SILENCIO_FINAL  = 10000;   // 10s mudo al terminar el fluir
    var SONIDO_SILENCIO_ENTRE  = 1500;    // silencio entre audios

    var SLIDESHOW = {
        items: [],
        index: 0,
        cont: null,
        ultimoAvance: 0,
        intervaloMs: 8000  // 8 segundos entre imágenes
    };

    // Rotador de videos: reproduce UN video (muted) y al terminar pasa al
    // siguiente. Tope por video para no colgarse en videos largos.
    var VIDEO_ROTADOR = {
        items: [],
        index: 0,
        cont: null,
        timer: null,
        maxMs: 45000  // ~45s por video
    };

    var VENTANA_CHAT = {
        tamano: 10,
        inicio: 0,
        ultimoInicio: -1
    };

    // Mapa Leaflet del bloque "Mapa": se inicializa una vez y se re-filtra
    // client-side con los mismos chips que los medios.
    var MAPA = {
        map: null,
        grupo: null,     // layerGroup de marcadores
        ctrl: null,      // contenedor del selector por municipio
        view: 'todos'    // municipio puntual o 'todos'
    };

    // Rotador de textos: muestra UN texto completo durante 30s y luego el siguiente.
    var TEXTO_ROTADOR = {
        items: [],
        index: 0,
        cont: null,
        timer: null,
        duracionMs: 30000
    };

    // Mínimo de palabras para considerar un texto (transcripción o texto importado)
    // como tal: por debajo no se muestra en el rotador de textos.
    var MIN_PALABRAS_TEXTO = 8;

    var FLOW = {
        activo: false,
        inicio: 0,
        duracionMs: 300000,  // 5 minutos
        horas: [],           // horas que recorre (copia al iniciar)
        ultimoSegundo: -1    // para refrescar botón cada 1s
    };

    // ═══════════════════════════════════════════════════════
    //  BLOQUES — definición y estado
    // ═══════════════════════════════════════════════════════

    var BLOQUES = [];
    var BLOQUES_TEMPLATE = [
        { id: 'colores',     tipo: 'selector', titulo: 'Colores',     w: 300, h: 140 },
        { id: 'horas',       tipo: 'selector', titulo: 'Horas',       w: 550, h: 260 },
        { id: 'provincias',  tipo: 'selector', titulo: 'Provincias',  w: 350, h: 160 },
        { id: 'municipios',  tipo: 'selector', titulo: 'Municipios',  w: 520, h: 480 },
        { id: 'tags',        tipo: 'selector', titulo: 'Tags',        w: 500, h: 380 },
        { id: 'imagenes',    tipo: 'media',    titulo: 'Im\u00e1genes', w: 860, h: 620 },
        { id: 'videos',      tipo: 'media',    titulo: 'Videos',      w: 520, h: 380 },
        { id: 'videos360',   tipo: 'media',    titulo: 'Videos 360\u00b0', w: 520, h: 400 },
        { id: 'textos',      tipo: 'media',    titulo: 'Textos',      w: 420, h: 300 },
        { id: 'sonidos',     tipo: 'media',    titulo: 'Sonidos',     w: 340, h: 240 },
        { id: 'mapa',        tipo: 'media',    titulo: 'Mapa',        w: 560, h: 420 },
        { id: 'comunicacion', tipo: 'media',   titulo: 'Comunicaci\u00f3n', w: 480, h: 420 }
    ];

    // ═══════════════════════════════════════════════════════
    //  PALETAS POR HORA — 24 momentos del día
    // ═══════════════════════════════════════════════════════

    var PALETTAS = [
        { bg:[8,8,18],      text:[110,120,140], accent:[50,60,90],    surface:[14,14,28],   slider:[40,50,80]  },
        { bg:[6,6,16],      text:[100,110,135], accent:[45,55,85],    surface:[12,12,26],   slider:[35,45,75]  },
        { bg:[5,5,15],      text:[95,105,130],  accent:[40,50,80],    surface:[11,11,24],   slider:[30,40,70]  },
        { bg:[5,5,15],      text:[90,100,125],  accent:[38,48,78],    surface:[10,10,22],   slider:[28,38,68]  },
        { bg:[12,10,22],    text:[100,100,120], accent:[60,50,80],    surface:[18,16,30],   slider:[50,42,70]  },
        { bg:[40,25,50],    text:[180,150,170], accent:[220,140,120], surface:[55,38,65],   slider:[180,100,90] },
        { bg:[80,45,55],    text:[240,200,190], accent:[255,160,100], surface:[100,60,70],  slider:[240,140,90] },
        { bg:[180,120,80],  text:[60,30,10],    accent:[255,180,80],  surface:[200,140,100],slider:[255,160,60] },
        { bg:[220,200,170], text:[60,50,30],    accent:[200,150,60],  surface:[235,220,195],slider:[220,160,60] },
        { bg:[230,225,210], text:[55,50,35],    accent:[180,140,60],  surface:[240,236,225],slider:[200,160,70] },
        { bg:[220,228,235], text:[40,55,70],    accent:[80,130,180],  surface:[235,240,248],slider:[80,130,180] },
        { bg:[228,238,245], text:[35,55,75],    accent:[70,140,200],  surface:[240,248,255],slider:[70,140,200] },
        { bg:[232,240,248], text:[30,55,78],    accent:[60,130,195],  surface:[242,250,255],slider:[60,130,195] },
        { bg:[235,228,215], text:[65,55,35],    accent:[200,150,60],  surface:[245,240,228],slider:[200,150,60] },
        { bg:[230,215,185], text:[75,55,25],    accent:[210,140,50],  surface:[240,228,205],slider:[210,140,50] },
        { bg:[225,200,160], text:[80,50,20],    accent:[220,130,40],  surface:[238,218,185],slider:[220,130,40] },
        { bg:[215,175,120], text:[70,40,10],    accent:[240,120,30],  surface:[230,195,150],slider:[240,120,30] },
        { bg:[200,130,70],  text:[60,25,5],     accent:[255,120,40],  surface:[220,155,100],slider:[255,120,40] },
        { bg:[160,80,55],   text:[240,180,160], accent:[255,100,50],  surface:[180,100,75], slider:[255,100,50] },
        { bg:[90,50,65],    text:[200,170,190], accent:[180,100,140], surface:[110,65,80],  slider:[160,90,120] },
        { bg:[35,25,48],    text:[140,130,160], accent:[100,80,130],  surface:[48,38,62],   slider:[90,70,120] },
        { bg:[18,18,32],    text:[120,120,150], accent:[70,70,110],   surface:[26,26,42],   slider:[60,60,100] },
        { bg:[12,12,25],    text:[110,115,140], accent:[55,60,95],    surface:[20,20,35],   slider:[48,52,85]  },
        { bg:[10,10,22],    text:[105,110,135], accent:[50,58,90],    surface:[16,16,30],   slider:[42,48,80]  }
    ];
    var NOMBRES_HORA = [
        'Madrugada','Madrugada','Madrugada','Madrugada',
        'Amanecer','Amanecer','Amanecer','Sol naciente',
        'Ma\u00f1ana','Ma\u00f1ana','Media ma\u00f1ana','Mediod\u00eda',
        'Mediod\u00eda','Tarde temprana','Tarde','Tarde',
        'Tarde tard\u00eda','Atardecer','Atardecer','Crep\u00fasculo',
        'Noche temprana','Noche','Noche','Noche'
    ];

    // ═══════════════════════════════════════════════════════
    //  INTERPOLACIÓN
    // ═══════════════════════════════════════════════════════

    function lerp(a, b, t) { return a + (b - a) * t; }
    function lerpColor(c1, c2, t) {
        return [
            Math.round(lerp(c1[0], c2[0], t)),
            Math.round(lerp(c1[1], c2[1], t)),
            Math.round(lerp(c1[2], c2[2], t))
        ];
    }
    function rgb(c) { return 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')'; }
    function interpolar(hora) {
        var h = ((hora % 24) + 24) % 24;
        var i0 = Math.floor(h);
        var i1 = (i0 + 1) % 24;
        var t = h - i0;
        var p0 = PALETTAS[i0];
        var p1 = PALETTAS[i1];
        return {
            bg:      lerpColor(p0.bg, p1.bg, t),
            text:    lerpColor(p0.text, p1.text, t),
            accent:  lerpColor(p0.accent, p1.accent, t),
            surface: lerpColor(p0.surface, p1.surface, t),
            slider:  lerpColor(p0.slider, p1.slider, t)
        };
    }

    // ═══════════════════════════════════════════════════════
    //  CANVAS + CÁMARA
    // ═══════════════════════════════════════════════════════

    var canvas = document.getElementById('lienzo');
    var ctx = canvas.getContext('2d');
    var dims = { w: 0, h: 0 };
    var cam = { tx: 0, ty: 0, scale: 1, zoomMin: 0.001, zoomMax: 5 };
    var drag = { active: false, lx: 0, ly: 0, ltx: 0, lty: 0 };

    var zoomSlider = document.getElementById('zoom-slider');
    var zoomLabel  = document.getElementById('zoom-label');
    var zoomInBtn  = document.getElementById('zoom-in');
    var zoomOutBtn = document.getElementById('zoom-out');

    var paleta = interpolar(12);
    var paletaTarget = paleta;
    var TASA = 0.08;

    // ═══════════════════════════════════════════════════════
    //  MUNDO FINITO — límites que contienen todos los bloques
    // ═══════════════════════════════════════════════════════

    var worldBounds = { x: 0, y: 0, w: 0, h: 0 };

    function calcularBounds() {
        var minX = Infinity, maxX = -Infinity;
        var minY = Infinity, maxY = -Infinity;
        BLOQUES.forEach(function(b) {
            if (b.mx < minX) minX = b.mx;
            if (b.mx + b.w > maxX) maxX = b.mx + b.w;
            if (b.my < minY) minY = b.my;
            if (b.my + b.h > maxY) maxY = b.my + b.h;
        });
        if (!isFinite(minX)) return;
        var pad = 40;
        worldBounds.x = minX - pad;
        worldBounds.y = minY - pad;
        worldBounds.w = (maxX - minX) + pad * 2;
        worldBounds.h = (maxY - minY) + pad * 2;
    }

    function ajustarProporcionMundo() {
        if (worldBounds.w === 0 || dims.w === 0) return;
        var ratioPantalla = dims.w / dims.h;
        var ratioMundo = worldBounds.w / worldBounds.h;
        if (ratioMundo > ratioPantalla) {
            // Mundo más ancho que la pantalla → aumentar altura
            var nuevoH = worldBounds.w / ratioPantalla;
            var diff = nuevoH - worldBounds.h;
            worldBounds.y -= diff / 2;
            worldBounds.h = nuevoH;
        } else {
            // Mundo más alto que la pantalla → aumentar ancho
            var nuevoW = worldBounds.h * ratioPantalla;
            var diff = nuevoW - worldBounds.w;
            worldBounds.x -= diff / 2;
            worldBounds.w = nuevoW;
        }
    }

    function aplicarLimitesCamara() {
        if (worldBounds.w === 0) return;
        // tx cuando el borde izquierdo del mundo toca el borde izq. del viewport
        var izq = -worldBounds.x * cam.scale;
        // tx cuando el borde derecho del mundo toca el borde der. del viewport
        var der = dims.w - (worldBounds.x + worldBounds.w) * cam.scale;
        // tx cuando el borde superior del mundo toca el borde sup. del viewport
        var sup = -worldBounds.y * cam.scale;
        // tx cuando el borde inferior del mundo toca el borde inf. del viewport
        var inf = dims.h - (worldBounds.y + worldBounds.h) * cam.scale;

        if (izq > der) {
            // Mundo más ancho que el viewport → permitir paneo limitado
            cam.tx = Math.max(der, Math.min(izq, cam.tx));
        } else {
            // Mundo entra en el viewport → centrar
            cam.tx = (izq + der) / 2;
        }
        if (sup > inf) {
            cam.ty = Math.max(inf, Math.min(sup, cam.ty));
        } else {
            cam.ty = (sup + inf) / 2;
        }
    }

    // ═══════════════════════════════════════════════════════
    //  ALGORITMO DE COLOCACIÓN ALEATORIA
    // ═══════════════════════════════════════════════════════

    function shuffle(arr) {
        for (var i = arr.length - 1; i > 0; i--) {
            var j = Math.floor(Math.random() * (i + 1));
            var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
        }
        return arr;
    }

    function haySuperposicion(b, excluir) {
        for (var i = 0; i < BLOQUES.length; i++) {
            var o = BLOQUES[i];
            if (o === b || o === excluir) continue;
            if (o.mx === 0 && o.my === 0) continue; // no colocado aún
            if (b.mx < o.mx + o.w + 1 && b.mx + b.w + 1 > o.mx &&
                b.my < o.my + o.h + 1 && b.my + b.h + 1 > o.my) {
                return true;
            }
        }
        return false;
    }

    var SEP = 8;  // separación mínima entre bloques

    function colocarBloques() {
        // ── TODOS los bloques en UNA zona compacta y centrada (más peso al centro).
        // Empaqueta en filas anchas → poca altura, mancha densa, horizontal.
        // El ancho de fila se deriva del área total: si la mancha quedara vertical
        // (ratio < 4:3) se amplía el ancho para "acostarla" (rango 4:3 a 8:5).
        var area = 0;
        BLOQUES.forEach(function(b) { area += b.w * b.h; });
        var anchoMax = Math.round(Math.sqrt(area * 1.5));
        var ids = shuffle(
            ['imagenes', 'videos', 'videos360', 'textos', 'sonidos', 'mapa', 'comunicacion',
             'colores', 'horas', 'provincias', 'municipios', 'tags']
        );

        var filaX = 0, filaY = 0, filaMaxH = 0, anchoFila = 0;
        ids.forEach(function(id) {
            var b = BLOQUES.filter(function(x){return x.id===id;})[0];
            if (anchoFila + b.w > anchoMax && anchoFila > 0) {
                filaX = 0;
                filaY += filaMaxH + SEP;
                filaMaxH = 0;
                anchoFila = 0;
            }
            b.mx = filaX;
            b.my = filaY;
            filaX += b.w + SEP;
            anchoFila += b.w + SEP;
            filaMaxH = Math.max(filaMaxH, b.h);
        });

        // Centrar: que el centro de la mancha quede en (0,0)
        var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        BLOQUES.forEach(function(b) {
            if (b.mx < minX) minX = b.mx;
            if (b.mx + b.w > maxX) maxX = b.mx + b.w;
            if (b.my < minY) minY = b.my;
            if (b.my + b.h > maxY) maxY = b.my + b.h;
        });
        var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
        BLOQUES.forEach(function(b) {
            b.mx -= Math.round(cx);
            b.my -= Math.round(cy);
        });
    }

    // ═══════════════════════════════════════════════════════
    //  SINCRO BLOQUES → HTML
    // ═══════════════════════════════════════════════════════

    function syncBlocks() {
        var mundo = document.getElementById('mundo');
        // Crear elementos de bloque que falten
        BLOQUES.forEach(function(b) {
            var el = document.getElementById('bloque-' + b.id);
            if (!el) {
                el = document.createElement('div');
                el.id = 'bloque-' + b.id;
                el.className = 'bloque' + (b.tipo === 'media' ? ' bloque-media' : '') + (b.tipo === 'selector' ? ' bloque-selector' : '');
                if (b.id === 'mapa') el.className += ' bloque-mapa';
                el.innerHTML = '<div class="bloque-titulo">' + b.titulo + '</div>'
                            + '<div class="bloque-contenido"></div>';
                mundo.appendChild(el);
                // Render contenido según tipo
                renderContenidoBloque(b.id, el.querySelector('.bloque-contenido'));
            }
            // Posición y escala
            var sx = Math.round(b.mx * cam.scale + cam.tx);
            var sy = Math.round(b.my * cam.scale + cam.ty);
            el.style.transform = 'translate(' + sx + 'px, ' + sy + 'px) scale(' + cam.scale + ')';
            el.style.width  = b.w + 'px';
            el.style.height = b.h + 'px';
            el.style.display = ''; // permite el display:flex del CSS .bloque
        });
    }

    function renderContenidoBloque(id, cont) {
        switch (id) {
            case 'colores':   renderChipsColores(cont); break;
            case 'horas':     renderChipsHoras(cont); break;
            case 'provincias': renderChipsProvincias(cont); break;
            case 'municipios': renderChipsMunicipios(cont); break;
            case 'tags':      renderTags(cont); break;
            case 'imagenes':
            case 'videos':
            case 'sonidos':
                renderMediosLista(id, cont);
                break;
            case 'videos360':
                renderVideos360(cont);
                break;
            case 'textos':
                renderTextos(cont);
                break;
            case 'comunicacion':
                renderComunicacion(cont);
                break;
            case 'mapa':
                renderMapa(cont);
                break;
            default:
                cont.innerHTML = '';
        }
    }

    function renderTextos(cont) {
        // Limpiar el rotador anterior
        if (TEXTO_ROTADOR.timer) { clearTimeout(TEXTO_ROTADOR.timer); TEXTO_ROTADOR.timer = null; }

        var res = (MEDIOS_FILTRADOS && MEDIOS_FILTRADOS.resultados) ? MEDIOS_FILTRADOS.resultados : {};

        // No se toma como texto nada corto: se exige al menos MIN_PALABRAS_TEXTO palabras.
        function contarPalabras(txt) {
            if (!txt) return 0;
            return txt.trim().split(/\s+/).filter(Boolean).length;
        }
        function esTextoValido(i) {
            return contarPalabras(i.transcripcion || i.descripcion || '') >= MIN_PALABRAS_TEXTO;
        }
        function conTranscripcion(i) {
            return !!i.transcripcion && esTextoValido(i);
        }

        // PRIORIDAD 1: los textos del viaje (type='text'), primero no-Telegram.
        var textos = Array.isArray(res.text) ? res.text.filter(esTextoValido) : [];

        // PRIORIDAD 2: transcripciones de audio, primero no-Telegram.
        var transcripcionesAudio = Array.isArray(res.audio) ? res.audio.filter(conTranscripcion) : [];

        // PRIORIDAD 3: transcripciones de video, primero no-Telegram.
        var transcripcionesVideo = Array.isArray(res.video) ? res.video.filter(conTranscripcion) : [];

        // Priorizar no-Telegram dentro de cada grupo (igual que en imágenes).
        function noTgPrimero(lista) {
            var sinTg = lista.filter(function(i) { return i.carpeta !== 'telegram'; });
            var deTg = lista.filter(function(i) { return i.carpeta === 'telegram'; });
            return sinTg.concat(deTg);
        }
        var items = noTgPrimero(textos)
            .concat(noTgPrimero(transcripcionesAudio))
            .concat(noTgPrimero(transcripcionesVideo));

        if (!items.length) {
            cont.innerHTML = '<div style="opacity:.2;font-size:.6rem;text-align:center;padding:.5rem">—</div>';
            return;
        }

        TEXTO_ROTADOR.items = items;
        TEXTO_ROTADOR.index = 0;
        TEXTO_ROTADOR.cont = cont;

        mostrarTextoActual();
    }

    // Muestra el texto actual completo del rotador y programa el siguiente.
    function mostrarTextoActual() {
        var t = TEXTO_ROTADOR.cont;
        if (!t) return;
        var item = TEXTO_ROTADOR.items[TEXTO_ROTADOR.index];
        // Origen: nombre del archivo fuente (basename de ruta_relativa)
        var origen = (item.ruta_relativa || '').split(/[\\/]/).pop() || '';
        var html = '<div style="display:flex;flex-direction:column;width:100%;flex:1;min-height:0">';
        // Cabecera del titulo solo si el texto tiene titulo propio
        if (item.titulo) {
            html += '<div style="flex-shrink:0;font-size:.68rem;font-weight:600;line-height:1.3;margin-bottom:.25rem;color:rgb(var(--ar),var(--ag),var(--ab))">'
                  + item.titulo + '</div>';
        }
        html += '<div style="flex:1;min-height:0;overflow-y:auto;font-size:.58rem;line-height:1.5;opacity:.9">'
              + (item.transcripcion || item.descripcion || '') + '</div>'
              + '<div style="flex-shrink:0;font-size:.42rem;opacity:.45;margin-top:.15rem;text-align:center">'
              + ((TEXTO_ROTADOR.index + 1) + '/' + TEXTO_ROTADOR.items.length);
        if (origen) {
            html += ' · ' + origen;
        }
        html += '</div></div>';
        t.innerHTML = html;

        // Programar el avance dentro de 30 segundos
        TEXTO_ROTADOR.timer = setTimeout(function() {
            TEXTO_ROTADOR.index = (TEXTO_ROTADOR.index + 1) % TEXTO_ROTADOR.items.length;
            mostrarTextoActual();
        }, TEXTO_ROTADOR.duracionMs);
    }

    function renderMediosLista(id, cont) {
        var tipoMap = { imagenes:'image', videos:'video', sonidos:'audio', textos:'text' };
        var tipo = tipoMap[id] || id;
        var items = (MEDIOS_FILTRADOS && MEDIOS_FILTRADOS.resultados && MEDIOS_FILTRADOS.resultados[tipo])
                    ? MEDIOS_FILTRADOS.resultados[tipo] : [];
        // Excluir videos 360 de la lista regular (tienen su propio bloque)
        if (tipo === 'video') items = items.filter(function(i){ return i.subtipo !== '360'; });

        if (!items.length) {
            if (tipo === 'audio') limpiarSeleccionAudios();
            if (tipo === 'image') limpiarSeleccionImagenes();
            cont.innerHTML = '<div style="opacity:.2;font-size:.6rem;text-align:center;padding:.5rem">—</div>';
            return;
        }

        if (tipo === 'image') {
            // Imágenes: slideshow (una imagen a la vez, cambia src)
            if (!items.length) {
                cont.innerHTML = '<div style="opacity:.2;font-size:.6rem;text-align:center;padding:.5rem">—</div>';
                return;
            }

            // Priorizar imágenes que NO son de Telegram
            var sinTelegram = items.filter(function(i) { return i.carpeta !== 'telegram'; });
            var deTelegram = items.filter(function(i) { return i.carpeta === 'telegram'; });
            var priorizadas = sinTelegram.concat(deTelegram);
            if (!priorizadas.length) {
                cont.innerHTML = '<div style="opacity:.2;font-size:.6rem;text-align:center;padding:.5rem">—</div>';
                return;
            }

            SLIDESHOW.items = priorizadas;
            SLIDESHOW.index = 0;
            SLIDESHOW.cont = cont;

            var primeraUrl = 'api/servir_medio.php?id=' + priorizadas[0].id;
            var html = '<div class="slideshow-wrap" style="display:flex;flex-direction:column;width:100%;height:100%;position:relative;overflow:hidden">'
                     + '<div class="slide-img-area" style="flex:1;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden">'
                     + '<img id="slide-actual" src="' + primeraUrl + '"'
                     + ' style="width:100%;height:100%;object-fit:contain;transition:opacity .6s ease">'
                     + '</div>'
                     + '<div class="slide-counter" style="position:absolute;top:.3rem;right:.4rem;font-size:.45rem;opacity:.5;background:rgba(0,0,0,.35);padding:.05rem .3rem;border-radius:2px;pointer-events:none">1/' + priorizadas.length + '</div>'
                     + '</div>';
            cont.innerHTML = html;
        } else if (tipo === 'audio') {
            // Sonidos: lista de audios del plan de reproducción (autoplay).
            SONIDO.items = items.slice();
            // Planificar la batería automáticamente cuando llegan los audios
            if (FLOW.activo) planificarAudios();
            var html = '<div style="display:flex;flex-direction:column;gap:.15rem;width:100%">';
            html += '<div style="font-size:.45rem;opacity:.45;line-height:1.2;padding:.1rem 0">'
                  + 'Autoplay · silencio 10s al inicio y al final del fluir</div>';
            items.slice(0, 8).forEach(function(item) {
                var desc = item.descripcion || '';
                var dur = item.duracion_seg ? ' · ' + item.duracion_seg.toFixed(1) + 's' : '';
                var corto = desc.length > 40 ? desc.slice(0, 37) + '...' : desc;
                html += '<div style="display:flex;gap:.3rem;align-items:center;padding:.12rem 0;'
                      + 'border-bottom:1px solid rgba(var(--tr),var(--tg),var(--tb),.06)">'
                      + '<span data-role="audio-num" style="flex-shrink:0;font-size:.45rem;opacity:.5;width:1.1rem;text-align:center"></span>'
                      + '<div style="flex:1;min-width:0">'
                      + '<div style="font-size:.5rem;opacity:.75;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
                      + (corto || (item.archivo || 'audio')) + '</div>'
                      + '<div style="font-size:.42rem;opacity:.4">autoplay' + dur + '</div>'
                      + '</div>'
                      + '</div>';
            });
            html += '</div>';
            cont.innerHTML = html;
        } else if (tipo === 'video') {
            // Videos: reproduce UN video muteado; al terminar pasa al siguiente.
            if (VIDEO_ROTADOR.timer) { clearTimeout(VIDEO_ROTADOR.timer); VIDEO_ROTADOR.timer = null; }
            VIDEO_ROTADOR.items = items.slice();
            VIDEO_ROTADOR.index = 0;
            VIDEO_ROTADOR.cont = cont;
            var html = '<div style="position:relative;width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;background:#000">'
                     + '<video id="video-actual" muted autoplay playsinline preload="metadata"'
                     + ' style="width:100%;height:100%;object-fit:contain"></video>'
                     + '<div class="slide-counter" style="position:absolute;top:.3rem;right:.4rem;font-size:.45rem;opacity:.5;background:rgba(0,0,0,.35);padding:.05rem .3rem;border-radius:2px;pointer-events:none">1/' + items.length + '</div>'
                     + '</div>';
            cont.innerHTML = html;
            var vid = document.getElementById('video-actual');
            if (vid) vid.addEventListener('ended', avanzarVideo);
            cargarVideoActual();
        } else {
            // Otros (texto): lista simple
            var html = '<div style="display:flex;flex-direction:column;gap:.15rem;width:100%">';
            items.forEach(function(item) {
                var desc = item.descripcion || item.archivo || '';
                if (desc.length > 80) desc = desc.slice(0, 77) + '...';
                html += '<div style="font-size:.5rem;opacity:.5;padding:.1rem 0;line-height:1.3">' + desc + '</div>';
            });
            html += '</div>';
            cont.innerHTML = html;
        }
    }

    // Carga el video actual del rotador y programa el tope de duración.
    function cargarVideoActual() {
        var vid = document.getElementById('video-actual');
        if (!vid || !VIDEO_ROTADOR.items.length) return;
        if (VIDEO_ROTADOR.timer) { clearTimeout(VIDEO_ROTADOR.timer); VIDEO_ROTADOR.timer = null; }
        var item = VIDEO_ROTADOR.items[VIDEO_ROTADOR.index];
        vid.muted = true;
        vid.src = 'api/servir_medio.php?id=' + item.id;
        vid.load();
        vid.play().catch(function() {});
        var counter = document.querySelector('#bloque-videos .slide-counter');
        if (counter) counter.textContent = (VIDEO_ROTADOR.index + 1) + '/' + VIDEO_ROTADOR.items.length;
        // Tope: si el video dura más que maxMs, avanzar igual.
        VIDEO_ROTADOR.timer = setTimeout(avanzarVideo, VIDEO_ROTADOR.maxMs);
    }

    function avanzarVideo() {
        if (!VIDEO_ROTADOR.items.length) return;
        if (VIDEO_ROTADOR.timer) { clearTimeout(VIDEO_ROTADOR.timer); VIDEO_ROTADOR.timer = null; }
        VIDEO_ROTADOR.index = (VIDEO_ROTADOR.index + 1) % VIDEO_ROTADOR.items.length;
        cargarVideoActual();
    }

    // ═══════════════════════════════════════════════════════
    //  TELEGRAM — bloque Comunicación
    // ═══════════════════════════════════════════════════════

    // Ensambla la lista plana de mensajes a partir de los paquetes por lugar.
    // Recorre los paquetes en el orden de selección: lugar1 completo, luego lugar2, etc.
    function listaPlanaTelegram() {
        var plana = [];
        (MENSAJES_TELEGRAM && MENSAJES_TELEGRAM.paquetes || []).forEach(function(paq) {
            (paq.mensajes || []).forEach(function(m) {
                plana.push({ lugar: paq.lugar, m: m });
            });
        });
        return plana;
    }

    // Hora local (0..24) de un date_utc de Telegram. El date termina en Z o con
    // offset; le restamos la zona del viaje (Argentina, UTC-3) para que la hora
    // coincida con la que usa el loop (interpHour).
    function horaMensaje(m) {
        var iso = m.date_utc || m.date || '';
        var hh = argsHora(iso);
        return hh;
    }
    // Extrae la fracción de hora (local) de una cadena ISO.
    function argsHora(iso) {
        var m = /T(\d{2}):(\d{2})/.exec(iso);
        if (!m) return null;
        var hora = parseInt(m[1], 10);
        var min = parseInt(m[2], 10);
        // Z o +00:00 → hora UTC; convertir a Argentina (UTC-3).
        if (/Z$|(\+00:00)$/.test(iso)) hora -= 3;
        if (hora < 0) hora += 24;
        return hora + min / 60;
    }

    function renderComunicacion(cont, inicio, cantidad) {
        var plana = listaPlanaTelegram();
        if (!plana.length) {
            cont.innerHTML = '';
            return;
        }
        if (inicio === undefined) inicio = 0;
        if (cantidad === undefined) cantidad = VENTANA_CHAT.tamano;
        var ventana = plana.slice(inicio, inicio + cantidad);
        var html = '<div class="tg-scroll" style="display:flex;flex-direction:column;gap:.1rem;width:100%;flex:1;min-height:0;overflow-y:auto;padding:.2rem .3rem">';
        var lugarPrevio = null;
        ventana.forEach(function(item) {
            var m = item.m;
            var fecha = m.date_utc || '';
            var hora = fecha.length > 16 ? fecha.slice(11, 16) : '';
            var fechaCorta = fecha.length > 10 ? fecha.slice(5, 10) : '';
            var nombre = m.from_name || 'Desconocido';
            var texto = m.text || '';
            if (texto.length > 150) texto = texto.slice(0, 147) + '...';
            var conFoto = m.fotos && m.fotos.length > 0;
            if (!conFoto && parseInt(m.has_media) === 1) {
                if (m.message_type === 'photo') conFoto = true;
            }
            var icono = '';
            if (!conFoto && parseInt(m.has_media) === 1) {
                if (m.message_type === 'photo') icono = '\uD83D\uDCF7 ';
                else if (m.message_type === 'video') icono = '\uD83C\uDFAC ';
                else if (m.message_type === 'voice') icono = '\uD83C\uDFA4 ';
                else icono = '\uD83D\uDCCE ';
            }
            // Separador de paquete: cuando cambia de lugar
            if (item.lugar !== lugarPrevio) {
                html += '<div style="margin:.25rem 0 .1rem;font-size:.48rem;letter-spacing:.08em;text-transform:uppercase;'
                      + 'opacity:.5;color:rgb(var(--ar),var(--ag),var(--ab));border-top:1px solid rgba(var(--tr),var(--tg),var(--tb),.12);padding-top:.15rem;font-weight:500">'
                      + (item.lugar || '—') + '</div>';
                lugarPrevio = item.lugar;
            }
            html += '<div class="tg-msg" style="font-size:.5rem;line-height:1.3;border-bottom:1px solid rgba(var(--tr),var(--tg),var(--tb),.08);padding:.1rem 0">'
                  + '<span style="opacity:.5;font-size:.45rem">' + fechaCorta + ' ' + hora + '</span> '
                  + '<strong style="opacity:.85">' + nombre + '</strong> '
                  + '<span style="opacity:.65">' + (icono || (conFoto ? '\uD83D\uDCF7 ' : '')) + texto + '</span>';
            if (conFoto) {
                var fotosIds = m.fotos && m.fotos.length ? m.fotos : (m.media_ids ? m.media_ids : []);
                if (fotosIds.length) {
                    html += '<div style="display:flex;gap:.15rem;margin-top:.1rem;flex-wrap:wrap">';
                    fotosIds.forEach(function(fid) {
                        html += '<img src="api/servir_medio.php?id=' + fid + '&thumb=1"'
                              + ' style="width:auto;height:2.8rem;max-width:5rem;object-fit:cover;border-radius:2px;border:1px solid rgba(var(--tr),var(--tg),var(--tb),.12);cursor:pointer"'
                              + ' onclick="window.open(\'api/servir_medio.php?id=' + fid + '\',\'_blank\')"'
                              + ' loading="lazy">';
                    });
                    html += '</div>';
                }
            }
            html += '</div>';
        });
        html += '</div>';
        cont.innerHTML = html;
    }

    function cargarMensajesTelegram(municipios) {
        if (!municipios || !municipios.length) {
            MENSAJES_TELEGRAM = null;
            renderComunicacionBlock();
            return;
        }
        // Cargar en paralelo todos los municipios elegidos; cada uno es un paquete.
        var promesas = municipios.map(function(m) {
            return fetch('api/mensajes_telegram.php?municipio=' + encodeURIComponent(m) + '&limite=200')
                .then(function(r) { return r.json(); })
                .then(function(data) { return { lugar: m, mensajes: data.mensajes || [] }; })
                .catch(function(e) {
                    console.warn('Error carga Telegram ' + m, e);
                    return { lugar: m, mensajes: [] };
                });
        });
        return Promise.all(promesas).then(function(paquetes) {
            paquetes = paquetes.filter(function(p) { return p.mensajes.length > 0; });
            MENSAJES_TELEGRAM = { paquetes: paquetes };
            VENTANA_CHAT.inicio = 0;
            VENTANA_CHAT.ultimoInicio = -1;
            renderComunicacionBlock();
        });
    }

    function renderComunicacionBlock() {
        var bloque = document.getElementById('bloque-comunicacion');
        if (!bloque) return;
        var cont = bloque.querySelector('.bloque-contenido');
        if (!cont) return;
        renderComunicacion(cont, VENTANA_CHAT.inicio, VENTANA_CHAT.tamano);
    }

    // ═══════════════════════════════════════════════════════
    //  MAPA — Leaflet integrado, filtrado por los chips
    // ═══════════════════════════════════════════════════════

    // Filtra los puntos de recorrido.php con los mismos chips que los medios.
    // Devuelve los puntos con lat/lon válidas que coinciden con la selección.
    function puntosFiltradosMapa() {
        var puntos = (DATOS_API && DATOS_API.puntos) ? DATOS_API.puntos : [];
        var munis = municipiosSeleccionados;
        var provs = provinciasSeleccionadas;
        var cols = coloresSeleccionados;
        var tags = tagsSeleccionados;
        var horas = horasSeleccionadas;

        var tagLower = tags.map(function(t) { return t.toLowerCase(); });

        return puntos.filter(function(p) {
            if (p.latitud === null || p.latitud === undefined ||
                p.longitud === null || p.longitud === undefined) return false;
            if (munis.length && munis.indexOf(p.municipio) === -1) return false;
            if (provs.length && provs.indexOf(p.provincia) === -1) return false;
            if (cols.length) {
                var c1 = p.color_1, c2 = p.color_2, c3 = p.color_3;
                var ok = cols.some(function(c) {
                    return c === c1 || c === c2 || c === c3;
                });
                if (!ok) return false;
            }
            if (tagLower.length) {
                var kw = (p.keywords || '').toLowerCase();
                var okTag = tagLower.some(function(t) {
                    return kw.indexOf(t) !== -1;
                });
                if (!okTag) return false;
            }
            if (horas.length) {
                // Franja [min,max] en hora local Argentina (UTC-3), igual que el PHP.
                var hh = parseInt((p.hora || '').slice(0, 2), 10);
                if (isNaN(hh)) return false;
                var local = ((hh - 3 + 24) % 24);
                var hmin = Math.min.apply(null, horas);
                var hmax = Math.max.apply(null, horas);
                if (local < hmin || local > hmax) return false;
            }
            return true;
        });
    }

    // Crea los marcadores para el conjunto de puntos dado y los encuadra.
    function dibujarPuntosMapa(puntos) {
        if (!MAPA.map || !MAPA.map.grupo) return;
        MAPA.map.grupo.clearLayers();
        if (!puntos.length) {
            MAPA.map.setView([-34.5, -64], 4);
            return;
        }
        var bounds = [];
        puntos.forEach(function(p) {
            var lat = parseFloat(p.latitud);
            var lon = parseFloat(p.longitud);
            if (isNaN(lat) || isNaN(lon)) return;
            bounds.push([lat, lon]);
            var hex = p.color_1_hex || '#888';
            var popup = '<div style="font-size:11px;min-width:120px">';
            if (p.tipo === 'image') {
                popup += '<img src="api/servir_medio.php?id=' + p.id + '&thumb=1"'
                       + ' style="max-width:180px;max-height:140px;display:block;margin-bottom:4px;border-radius:2px">';
            }
            if (p.descripcion) popup += '<div style="margin-bottom:3px;opacity:.9">' + p.descripcion + '</div>';
            if (p.municipio) popup += '<div style="opacity:.55;font-size:10px">' + p.municipio
                                   + (p.provincia ? ' · ' + p.provincia : '') + '</div>';
            popup += '</div>';
            L.circleMarker([lat, lon], {
                radius: 4,
                color: '#fff',
                weight: 1,
                fillColor: hex,
                fillOpacity: 0.9
            }).addTo(MAPA.map.grupo).bindPopup(popup, { maxWidth: 240 });
        });
        if (bounds.length) MAPA.map.fitBounds(bounds, { padding: [12, 12] });
    }

    // Re-filtra el mapa según la selección actual + el municipio puntual elegido.
    function actualizarMapa() {
        var todos = puntosFiltradosMapa();
        var puntos = todos;
        if (MAPA.view !== 'todos') {
            puntos = todos.filter(function(p) { return p.municipio === MAPA.view; });
        }
        dibujarPuntosMapa(puntos);
        renderSelectorMunicipios(todos);
    }

    // Selector segmentado "Todos / <municipios>" arriba del mapa.
    function renderSelectorMunicipios(puntos) {
        if (!MAPA.ctrl) return;
        var munis = [];
        var visto = {};
        puntos.forEach(function(p) {
            if (p.municipio && !visto[p.municipio]) {
                visto[p.municipio] = true;
                munis.push(p.municipio);
            }
        });
        var html = '<button type="button" class="mapa-ctrl-btn' + (MAPA.view === 'todos' ? ' activo' : '') + '" data-mapa-muni="todos">Todos</button>';
        munis.forEach(function(m) {
            html += '<button type="button" class="mapa-ctrl-btn' + (MAPA.view === m ? ' activo' : '') + '" data-mapa-muni="' + m + '">' + m + '</button>';
        });
        MAPA.ctrl.innerHTML = html;
        MAPA.ctrl.querySelectorAll('[data-mapa-muni]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                MAPA.view = this.dataset.mapaMuni;
                actualizarMapa();
            });
        });
    }

    function crearMapaLeaflet(el) {
        var map = L.map(el, { zoomControl: false, attributionControl: true }).setView([-34.5, -64], 4);
        L.control.zoom({ position: 'topright' }).addTo(map);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
            subdomains: 'abcd',
            maxZoom: 19
        }).addTo(map);
        map.grupo = L.layerGroup().addTo(map);
        return map;
    }

    function renderMapa(cont) {
        // El mapa ocupa todo el cuadro; el selector por municipio va como overlay.
        cont.innerHTML = '<div class="mapa-wrap" style="position:absolute;inset:0">'
                       + '<div id="mapa-leaflet" style="position:absolute;inset:0"></div>'
                       + '<div class="mapa-ctrl" style="position:absolute;top:.25rem;left:.25rem;right:.25rem;display:flex;flex-wrap:wrap;gap:.2rem;justify-content:center;pointer-events:none"></div>'
                       + '</div>';
        var el = cont.querySelector('#mapa-leaflet');
        if (MAPA.map) { MAPA.map.remove(); MAPA.map = null; }
        MAPA.map = crearMapaLeaflet(el);
        MAPA.ctrl = cont.querySelector('.mapa-ctrl');
        setTimeout(function() {
            if (MAPA.map) MAPA.map.invalidateSize();
            actualizarMapa();
        }, 0);
    }

    function actualizarVentanaChat(horaActual) {
        if (!FLOW.activo) return;
        var plana = listaPlanaTelegram();
        if (!plana.length) return;

        // Hora de referencia del loop (0..24), normalizada.
        var h = ((horaActual % 24) + 24) % 24;

        // Encontrar el mensaje cuya hora real de llegada (local) está más cerca
        // de la hora que corre en el loop, para que la ventana "siga" el día.
        // Si no hay horas parseables, caemos al avance lineal por proporción.
        var mejor = -1, mejorDif = Infinity;
        for (var i = 0; i < plana.length; i++) {
            var hh = horaMensaje(plana[i].m);
            if (hh === null) continue;
            var dif = Math.abs(hh - h);
            if (dif > 12) dif = 24 - dif;   // circular: 23:00 y 01:00 están cerca
            if (dif < mejorDif) { mejorDif = dif; mejor = i; }
        }

        var total = plana.length;
        var tamano = VENTANA_CHAT.tamano;
        var maxInicio = Math.max(0, total - tamano);
        var nuevoInicio;
        if (mejor === -1) {
            // Sin horas: avance lineal proporcional a la hora del día.
            nuevoInicio = Math.round(Math.min(1, Math.max(0, h / 24)) * maxInicio);
        } else {
            nuevoInicio = Math.min(mejor, maxInicio);
        }

        if (nuevoInicio === VENTANA_CHAT.ultimoInicio) return; // sin cambios

        VENTANA_CHAT.inicio = nuevoInicio;
        VENTANA_CHAT.ultimoInicio = nuevoInicio;
        renderComunicacionBlock();
    }

    // ═══════════════════════════════════════════════════════
    //  SLIDESHOW — avance automático de imágenes
    // ═══════════════════════════════════════════════════════

    function avanzarSlideshow() {
        if (!SLIDESHOW.items || !SLIDESHOW.items.length || !SLIDESHOW.cont) return;
        var img = document.getElementById('slide-actual');
        if (!img) return;

        // Calcular el próximo índice
        var prox = (SLIDESHOW.index + 1) % SLIDESHOW.items.length;

        // Fade out
        img.style.opacity = '0';

        setTimeout(function() {
            SLIDESHOW.index = prox;
            var item = SLIDESHOW.items[prox];
            img.onload = function() {
                img.style.opacity = '1';
                img.onload = null;
            };
            img.src = 'api/servir_medio.php?id=' + item.id;
        }, 300);

        // Actualizar contador
        var counter = SLIDESHOW.cont.querySelector('.slide-counter');
        if (counter) {
            counter.textContent = (prox + 1) + '/' + SLIDESHOW.items.length;
        }
    }

    function reiniciarSlideshow() {
        if (!SLIDESHOW.cont || !SLIDESHOW.items.length) return;
        SLIDESHOW.index = 0;
        SLIDESHOW.ultimoAvance = 0;
        SLIDESHOW.items = SLIDESHOW.items; // mantener referencia
        var img = document.getElementById('slide-actual');
        if (img) {
            var item0 = SLIDESHOW.items[0];
            img.src = 'api/servir_medio.php?id=' + item0.id;
            img.style.opacity = '1';
        }
        var counter = SLIDESHOW.cont.querySelector('.slide-counter');
        if (counter) counter.textContent = '1/' + SLIDESHOW.items.length;
    }

    // ═══════════════════════════════════════════════════════
    //  CHIPS: COLORES
    // ═══════════════════════════════════════════════════════

    function renderChipsColores(cont) {
        var html = '<div style="display:flex;flex-wrap:wrap;gap:.3rem;justify-content:stretch;align-content:stretch;align-items:stretch;flex:1;min-height:0;width:100%">';
        COLORES.forEach(function(c) {
            var activo = coloresSeleccionados.indexOf(c.nombre) !== -1 ? ' activo' : '';
            html += '<button class="chip' + activo + '" data-accion="toggle-color" data-valor="' + c.nombre + '">'
                  + '<span class="chip-bola" style="background:' + c.hex + '"></span>'
                  + c.nombre
                  + '</button>';
        });
        html += '</div>';
        html += '<div style="display:flex;align-items:center;gap:.3rem;width:100%;flex-shrink:0">'
              + '<span class="info-filtro" id="info-colores" style="margin-left:0">Todos</span>'
              + '<button type="button" class="chip-limpiar" data-accion="limpiar-colores" title="Desmarcar todos">\u2715</button>'
              + '</div>';
        cont.innerHTML = html;
        // Bind events
        cont.querySelectorAll('[data-accion="toggle-color"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                toggleColor(this.dataset.valor);
            });
        });
        var btnLimpiar = cont.querySelector('[data-accion="limpiar-colores"]');
        if (btnLimpiar) btnLimpiar.addEventListener('click', function() { limpiarSeleccion('colores'); });
        actualizarInfoColores();
    }

    function toggleColor(nombre) {
        var idx = coloresSeleccionados.indexOf(nombre);
        if (idx === -1) coloresSeleccionados.push(nombre);
        else coloresSeleccionados.splice(idx, 1);
        // Actualizar chips visualmente
        document.querySelectorAll('#bloque-colores [data-accion="toggle-color"]').forEach(function(btn) {
            if (btn.dataset.valor === nombre) btn.classList.toggle('activo');
        });
        actualizarInfoColores();
    }

    function actualizarInfoColores() {
        var info = document.getElementById('info-colores');
        if (!info) return;
        if (coloresSeleccionados.length === 0) info.textContent = 'Todos';
        else if (coloresSeleccionados.length === 1) info.textContent = coloresSeleccionados[0];
        else info.textContent = coloresSeleccionados.length + ' colores';
    }

    // Desmarca todos los chips de un bloque selector.
    function limpiarSeleccion(grupo) {
        if (grupo === 'colores') coloresSeleccionados = [];
        else if (grupo === 'horas') horasSeleccionadas = [];
        else if (grupo === 'provincias') {
            provinciasSeleccionadas = [];
            municipiosSeleccionados = [];   // cascada: sin provincias, sin municipios
        }
        else if (grupo === 'municipios') municipiosSeleccionados = [];
        else if (grupo === 'tags') tagsSeleccionados = [];

        rerenderBloque(grupo);
        if (grupo === 'provincias') rerenderBloque('municipios');
        if (grupo === 'horas') {
            // Volver la paleta a un valor neutral (mediodía)
            horaActual = 12;
            paletaTarget = interpolar(horaActual);
        }
    }

    // ═══════════════════════════════════════════════════════
    //  CHIPS: HORAS
    // ═══════════════════════════════════════════════════════

    function renderChipsHoras(cont) {
        var html = '<div style="display:grid;grid-template-columns:repeat(6, 1fr);grid-template-rows:repeat(4, 1fr);gap:.25rem;flex:1;min-height:0;width:100%">';
        HORAS.forEach(function(h) {
            var p = PALETTAS[h];
            var hh = (h < 10 ? '0' : '') + h;
            var activo = horasSeleccionadas.indexOf(h) !== -1 ? ' activo' : '';
            html += '<button class="chip-hora' + activo + '" data-accion="toggle-hora" data-valor="' + h + '"'
                  + ' style="--chip-bg:' + p.bg.join(',') + ';--chip-txt:' + p.text.join(',') + '">'
                  + hh + ':00'
                  + '</button>';
        });
        html += '</div>';
        html += '<div style="display:flex;align-items:center;gap:.3rem;width:100%;flex-shrink:0">'
              + '<span class="info-filtro" id="info-horas" style="margin-left:0">Ninguna</span>'
              + '<button type="button" class="chip-limpiar" data-accion="limpiar-horas" title="Desmarcar todos">\u2715</button>'
              + '</div>';
        cont.innerHTML = html;
        cont.querySelectorAll('[data-accion="toggle-hora"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                toggleHora(parseInt(this.dataset.valor));
            });
        });
        var btnLimpiar = cont.querySelector('[data-accion="limpiar-horas"]');
        if (btnLimpiar) btnLimpiar.addEventListener('click', function() { limpiarSeleccion('horas'); });
        actualizarInfoHoras();
    }

    function toggleHora(h) {
        var idx = horasSeleccionadas.indexOf(h);
        if (idx === -1) {
            horasSeleccionadas.push(h);
        } else {
            horasSeleccionadas.splice(idx, 1);
        }
        // La paleta sigue la última hora clickeada
        horaActual = h;

        document.querySelectorAll('#bloque-horas [data-accion="toggle-hora"]').forEach(function(btn) {
            if (parseInt(btn.dataset.valor) === h) btn.classList.toggle('activo');
        });
        actualizarInfoHoras();
        // Transicionar paleta
        paletaTarget = interpolar(horaActual);
    }

    function actualizarInfoHoras() {
        var info = document.getElementById('info-horas');
        if (!info) return;
        if (horasSeleccionadas.length === 0) info.textContent = 'Ninguna';
        else {
            var txt = horasSeleccionadas.slice(0, 3).map(function(h) {
                return (h < 10 ? '0' : '') + h + ':00';
            }).join(' ');
            if (horasSeleccionadas.length > 3) txt += ' +' + (horasSeleccionadas.length - 3);
            info.textContent = txt;
        }
    }

    // ═══════════════════════════════════════════════════════
    //  CHIPS: PROVINCIAS
    // ═══════════════════════════════════════════════════════

    function renderChipsProvincias(cont) {
        var html = '<div style="display:flex;flex-wrap:wrap;gap:.3rem;justify-content:stretch;align-content:stretch;align-items:stretch;flex:1;min-height:0;width:100%">';
        PROVINCIAS.forEach(function(p) {
            var activo = provinciasSeleccionadas.indexOf(p.nombre) !== -1 ? ' activo' : '';
            html += '<button class="chip' + activo + '" data-accion="toggle-provincia" data-valor="' + p.nombre + '">'
                  + p.nombre
                  + '</button>';
        });
        html += '</div>';
        html += '<div style="display:flex;align-items:center;gap:.3rem;width:100%;flex-shrink:0">'
              + '<span class="info-filtro" id="info-provincias" style="margin-left:0">Todas</span>'
              + '<button type="button" class="chip-limpiar" data-accion="limpiar-provincias" title="Desmarcar todos">\u2715</button>'
              + '</div>';
        cont.innerHTML = html;
        cont.querySelectorAll('[data-accion="toggle-provincia"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                toggleProvincia(this.dataset.valor);
            });
        });
        var btnLimpiar = cont.querySelector('[data-accion="limpiar-provincias"]');
        if (btnLimpiar) btnLimpiar.addEventListener('click', function() { limpiarSeleccion('provincias'); });
        actualizarInfoProvincias();
    }

    // Municipios que pertenecen a una provincia (según el mapa cargado de la API).
    function municipiosDeProvincia(provincia) {
        return MUNICIPIOS.filter(function(m) {
            return MUNICIPIOS_POR_PROVINCIA[m] === provincia;
        });
    }

    function toggleProvincia(nombre) {
        var idx = provinciasSeleccionadas.indexOf(nombre);
        if (idx === -1) {
            provinciasSeleccionadas.push(nombre);
            // Al seleccionar la provincia, se seleccionan sus municipios.
            municipiosDeProvincia(nombre).forEach(function(m) {
                if (municipiosSeleccionados.indexOf(m) === -1) {
                    municipiosSeleccionados.push(m);
                }
            });
        } else {
            provinciasSeleccionadas.splice(idx, 1);
            // Al deseleccionar la provincia, se deseleccionan sus municipios.
            var restantes = [];
            municipiosSeleccionados.forEach(function(m) {
                if (MUNICIPIOS_POR_PROVINCIA[m] !== nombre) restantes.push(m);
            });
            municipiosSeleccionados = restantes;
        }
        document.querySelectorAll('#bloque-provincias [data-accion="toggle-provincia"]').forEach(function(btn) {
            if (btn.dataset.valor === nombre) btn.classList.toggle('activo');
        });
        actualizarInfoProvincias();
        // Reflejar los municipios marcados/desmarcados en su bloque.
        rerenderBloque('municipios');
    }

    function actualizarInfoProvincias() {
        var info = document.getElementById('info-provincias');
        if (!info) return;
        if (provinciasSeleccionadas.length === 0) info.textContent = 'Todas';
        else if (provinciasSeleccionadas.length === 1) info.textContent = provinciasSeleccionadas[0];
        else info.textContent = provinciasSeleccionadas.length + ' provincias';
    }

    // ═══════════════════════════════════════════════════════
    //  CHIPS: MUNICIPIOS
    // ═══════════════════════════════════════════════════════

    function renderChipsMunicipios(cont) {
        var html = '<div style="display:flex;flex-wrap:wrap;gap:.2rem .35rem;justify-content:stretch;align-content:stretch;align-items:stretch;flex:1;min-height:0;width:100%">';
        MUNICIPIOS.forEach(function(m) {
            var activo = municipiosSeleccionados.indexOf(m) !== -1 ? ' activo' : '';
            html += '<button class="chip' + activo + '" data-accion="toggle-municipio" data-valor="' + m + '">'
                  + m
                  + '</button>';
        });
        html += '</div>';
        html += '<div style="display:flex;align-items:center;gap:.3rem;width:100%;flex-shrink:0">'
              + '<span class="info-filtro" id="info-municipios" style="margin-left:0">Todos</span>'
              + '<button type="button" class="chip-limpiar" data-accion="limpiar-municipios" title="Desmarcar todos">\u2715</button>'
              + '</div>';
        cont.innerHTML = html;
        cont.querySelectorAll('[data-accion="toggle-municipio"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                toggleMunicipio(this.dataset.valor);
            });
        });
        var btnLimpiar = cont.querySelector('[data-accion="limpiar-municipios"]');
        if (btnLimpiar) btnLimpiar.addEventListener('click', function() { limpiarSeleccion('municipios'); });
        actualizarInfoMunicipios();
    }

    function toggleMunicipio(nombre) {
        var idx = municipiosSeleccionados.indexOf(nombre);
        if (idx === -1) municipiosSeleccionados.push(nombre);
        else municipiosSeleccionados.splice(idx, 1);
        document.querySelectorAll('#bloque-municipios [data-accion="toggle-municipio"]').forEach(function(btn) {
            if (btn.dataset.valor === nombre) btn.classList.toggle('activo');
        });
        actualizarInfoMunicipios();
    }

    function actualizarInfoMunicipios() {
        var info = document.getElementById('info-municipios');
        if (!info) return;
        if (municipiosSeleccionados.length === 0) info.textContent = 'Todos';
        else if (municipiosSeleccionados.length === 1) info.textContent = municipiosSeleccionados[0];
        else info.textContent = municipiosSeleccionados.length + ' municipios';
    }

    function rerenderBloque(id) {
        var bloque = document.getElementById('bloque-' + id);
        if (!bloque) return;
        var cont = bloque.querySelector('.bloque-contenido');
        if (cont) renderContenidoBloque(id, cont);
    }

    // ═══════════════════════════════════════════════════════
    //  TAGS
    // ═══════════════════════════════════════════════════════

    function renderTags(cont) {
        var tags = TAGS_API || [];
        if (!tags.length) {
            cont.innerHTML = '<div style="font-size:.5rem;opacity:.4;padding:.3rem">Sin datos</div>';
            return;
        }
        var html = '<div class="tag-cloud" style="display:flex;flex-wrap:wrap;gap:.12rem .2rem;justify-content:flex-start;align-content:flex-start;align-items:center;flex:1;min-height:0;padding:.15rem">';
        tags.forEach(function(t) {
            var activo = tagsSeleccionados.indexOf(t.tag) !== -1 ? ' activo' : '';
            // Todas las etiquetas con el MISMO tamaño (sin tamaño por peso/cantidad);
            // el CSS (.tag-item flex:1) las estira para llenar el cuadro que les toca.
            html += '<button type="button" class="tag-item chip' + activo + '"'
                  + ' data-accion="toggle-tag" data-valor="' + t.tag + '"'
                  + ' style="font-size:.55rem;opacity:.85;cursor:pointer;font-family:inherit;letter-spacing:.02em;line-height:1.2;padding:.08rem .15rem">'
                  + t.tag
                  + '</button>';
        });
        html += '</div>';
        html += '<div style="display:flex;align-items:center;gap:.3rem;width:100%;flex-shrink:0">'
              + '<span class="info-filtro" id="info-tags" style="margin-left:0">Ninguno</span>'
              + '<button type="button" class="chip-limpiar" data-accion="limpiar-tags" title="Desmarcar todos">\u2715</button>'
              + '</div>';
        cont.innerHTML = html;
        cont.querySelectorAll('[data-accion="toggle-tag"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                toggleTag(this.dataset.valor);
            });
        });
        var btnLimpiar = cont.querySelector('[data-accion="limpiar-tags"]');
        if (btnLimpiar) btnLimpiar.addEventListener('click', function() { limpiarSeleccion('tags'); });
        actualizarInfoTags();
    }

    function toggleTag(nombre) {
        var idx = tagsSeleccionados.indexOf(nombre);
        if (idx === -1) tagsSeleccionados.push(nombre);
        else tagsSeleccionados.splice(idx, 1);
        document.querySelectorAll('#bloque-tags [data-accion="toggle-tag"]').forEach(function(btn) {
            if (btn.dataset.valor === nombre) btn.classList.toggle('activo');
        });
        actualizarInfoTags();
    }

    function actualizarInfoTags() {
        var info = document.getElementById('info-tags');
        if (!info) return;
        if (!tagsSeleccionados.length) info.textContent = 'Ninguno';
        else if (tagsSeleccionados.length === 1) info.textContent = tagsSeleccionados[0];
        else info.textContent = tagsSeleccionados.length + ' tags';
    }

    // ═══════════════════════════════════════════════════════
    //  FLOW — iniciar, actualizar, detener
    // ═══════════════════════════════════════════════════════

    function obtenerFiltrosActivos() {
        var params = {};
        if (municipiosSeleccionados.length) params.municipio = municipiosSeleccionados.join(',');
        if (coloresSeleccionados.length) params.color = coloresSeleccionados.join(',');
        if (provinciasSeleccionadas.length) params.provincia = provinciasSeleccionadas.join(',');
        if (tagsSeleccionados.length) params.tag = tagsSeleccionados.join(',');
        if (horasSeleccionadas.length) params.horas = horasSeleccionadas.join(',');
        return params;
    }

    function cargarMediosFiltrados() {
        var requestId = ++MEDIOS_REQUEST_ID;
        limpiarSeleccionAudios();
        var params = obtenerFiltrosActivos();
        var qs = Object.keys(params).map(function(k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
        }).join('&');
        // Dos fetch en paralelo: general (imágenes, videos, audios, textos) y 360
        var qsGeneral = qs ? qs + '&' : '';
        qsGeneral += 'limite=30&tipo=image,video,audio,text';
        var qs360 = qs ? qs + '&' : '';
        qs360 += 'limite=20&tipo=video&subtipo=360';

        var fetchGeneral = fetch('api/medios_filtrados.php?' + qsGeneral)
            .then(function(r) { return r.json(); })
            .catch(function(e) {
                console.warn('Error cargando medios filtrados', e);
                return null;
            });
        var fetch360 = fetch('api/medios_filtrados.php?' + qs360)
            .then(function(r) { return r.json(); })
            .catch(function(e) {
                console.warn('Error cargando medios 360', e);
                return null;
            });

        return Promise.all([fetchGeneral, fetch360]).then(function(resultados) {
            if (requestId !== MEDIOS_REQUEST_ID) return;
            MEDIOS_FILTRADOS = resultados[0];
            MEDIOS_360 = resultados[1];
            renderMediaBlocks();
        });
    }

    function renderMediaBlocks() {
        // Re-renderear todos los bloques de medios
        ['imagenes', 'videos', 'videos360', 'sonidos', 'textos', 'comunicacion'].forEach(function(id) {
            var bloque = document.getElementById('bloque-' + id);
            if (!bloque) return;
            var cont = bloque.querySelector('.bloque-contenido');
            if (!cont) return;
            renderContenidoBloque(id, cont);
        });
    }

    // ═══════════════════════════════════════════════════════
    //  VIDEOS 360° — lista + visor Three.js
    // ═══════════════════════════════════════════════════════

    function fmtDuracion(seg) {
        if (!seg) return '';
        var m = Math.floor(seg / 60), s = Math.floor(seg % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    function renderVideos360(cont) {
        var items = (MEDIOS_360 && MEDIOS_360.resultados && MEDIOS_360.resultados.video)
                    ? MEDIOS_360.resultados.video : [];
        if (!items.length) {
            cont.innerHTML = '<div style="opacity:.2;font-size:.6rem;text-align:center;padding:.5rem">\u2014</div>';
            return;
        }
        var html = '<div style="display:flex;flex-direction:column;gap:.2rem;width:100%;padding:.1rem 0">';
        items.forEach(function(item) {
            var nombre = item.archivo || '';
            var desc = item.descripcion || '';
            if (desc.length > 30) desc = desc.slice(0, 27) + '...';
            var dur = fmtDuracion(item.duracion_seg);
            html += '<button type="button" class="item-360" data-id="' + item.id + '" title="' + nombre + '">'
                  + '<span class="badge-360">360\u00b0</span>'
                  + '<span class="item-360-text">' + (desc || nombre) + '</span>'
                  + (dur ? '<span class="item-360-dur">' + dur + '</span>' : '')
                  + '</button>';
        });
        html += '</div>';
        cont.innerHTML = html;
        cont.querySelectorAll('.item-360').forEach(function(btn) {
            btn.addEventListener('click', function() { abrirVisor360(parseInt(this.dataset.id, 10)); });
        });
    }

    // ── Visor 360° fullscreen (Three.js) ──
    var VISOR = {
        abierto: false,
        raf: 0,
        video: null,
        renderer: null,
        scene: null,
        camera: null,
        texture: null,
        sphere: null,
        geometry: null,
        material: null,
        yaw: 0,
        pitch: 0,
        dragging: false,
        dragX: 0,
        dragY: 0,
        autoRotate: true,
        _onResize: null,
        _onPointerDown: null,
        _onPointerMove: null,
        _onPointerUp: null,
        _onWheel: null
    };

    function abrirVisor360(id) {
        if (typeof THREE === 'undefined') {
            console.warn('Three.js no cargado — visor 360\u00b0 no disponible');
            return;
        }
        if (VISOR.abierto) cerrarVisor360();

        // Buscar el item para el título
        var titulo = '';
        var items = (MEDIOS_360 && MEDIOS_360.resultados && MEDIOS_360.resultados.video) || [];
        for (var i = 0; i < items.length; i++) {
            if (items[i].id === id) { titulo = items[i].archivo || items[i].descripcion || ''; break; }
        }

        // Crear overlay
        var overlay = document.getElementById('visor-360');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'visor-360';
            overlay.innerHTML = '<div class="visor-titulo"></div>'
                              + '<button class="visor-close" type="button">\u2715</button>'
                              + '<div class="visor-hint">Arrastr\u00e1 para mirar \u00b7 rueda para zoom</div>';
            document.body.appendChild(overlay);
        }
        overlay.querySelector('.visor-titulo').textContent = titulo;
        overlay.classList.add('visible');

        // Cerrar
        overlay.querySelector('.visor-close').onclick = function() { cerrarVisor360(); };

        // Video element
        var video = document.createElement('video');
        video.loop = true;
        video.playsInline = true;
        video.muted = true;
        video.autoplay = true;
        video.preload = 'auto';
        video.src = 'api/servir_medio.php?id=' + id;
        video.play().catch(function() {});
        VISOR.video = video;

        // Three.js scene
        var w = window.innerWidth, h = window.innerHeight;
        var scene = new THREE.Scene();
        var camera = new THREE.PerspectiveCamera(75, w / h, 0.1, 10);
        camera.position.set(0, 0, 0);
        camera.rotation.order = 'YXZ';
        camera.rotation.y = VISOR.yaw;
        camera.rotation.x = VISOR.pitch;

        var texture = new THREE.VideoTexture(video);
        texture.minFilter = THREE.LinearFilter;
        texture.magFilter = THREE.LinearFilter;
        texture.generateMipmaps = false;
        VISOR.texture = texture;

        var geometry = new THREE.SphereGeometry(1, 64, 32);
        var material = new THREE.MeshBasicMaterial({ map: texture, side: THREE.BackSide });
        var sphere = new THREE.Mesh(geometry, material);
        scene.add(sphere);

        var renderer = new THREE.WebGLRenderer();
        renderer.setSize(w, h);
        renderer.setPixelRatio(window.devicePixelRatio);
        renderer.domElement.style.touchAction = 'none';
        overlay.insertBefore(renderer.domElement, overlay.firstChild);

        VISOR.scene = scene;
        VISOR.camera = camera;
        VISOR.sphere = sphere;
        VISOR.geometry = geometry;
        VISOR.material = material;
        VISOR.renderer = renderer;
        VISOR.abierto = true;

        // ── Animación ──
        function animate() {
            VISOR.raf = requestAnimationFrame(animate);
            if (!VISOR.abierto) return;
            if (!VISOR.dragging && VISOR.autoRotate) VISOR.yaw += 0.0007;
            VISOR.camera.rotation.y = VISOR.yaw;
            VISOR.camera.rotation.x = VISOR.pitch;
            if (VISOR.video && VISOR.video.readyState >= 2) VISOR.texture.needsUpdate = true;
            VISOR.renderer.render(VISOR.scene, VISOR.camera);
        }
        animate();

        // ── Drag (pointer events) ──
        var canvas = renderer.domElement;
        function onPointerDown(e) {
            VISOR.dragging = true;
            VISOR.dragX = e.clientX;
            VISOR.dragY = e.clientY;
            canvas.setPointerCapture(e.pointerId);
        }
        function onPointerMove(e) {
            if (!VISOR.dragging) return;
            var dx = e.clientX - VISOR.dragX;
            var dy = e.clientY - VISOR.dragY;
            VISOR.dragX = e.clientX;
            VISOR.dragY = e.clientY;
            VISOR.yaw -= dx * 0.005;
            VISOR.pitch -= dy * 0.005;
            if (VISOR.pitch > 1.55) VISOR.pitch = 1.55;
            if (VISOR.pitch < -1.55) VISOR.pitch = -1.55;
        }
        function onPointerUp() { VISOR.dragging = false; }
        canvas.addEventListener('pointerdown', onPointerDown);
        canvas.addEventListener('pointermove', onPointerMove);
        canvas.addEventListener('pointerup', onPointerUp);
        VISOR._onPointerDown = onPointerDown;
        VISOR._onPointerMove = onPointerMove;
        VISOR._onPointerUp = onPointerUp;

        // ── Wheel → zoom (fov) ──
        function onWheel(e) {
            e.preventDefault();
            VISOR.camera.fov += e.deltaY * 0.05;
            if (VISOR.camera.fov < 30) VISOR.camera.fov = 30;
            if (VISOR.camera.fov > 110) VISOR.camera.fov = 110;
            VISOR.camera.updateProjectionMatrix();
        }
        canvas.addEventListener('wheel', onWheel, { passive: false });
        VISOR._onWheel = onWheel;

        // ── Resize ──
        function onResize() {
            if (!VISOR.abierto) return;
            var w2 = window.innerWidth, h2 = window.innerHeight;
            VISOR.camera.aspect = w2 / h2;
            VISOR.camera.updateProjectionMatrix();
            VISOR.renderer.setSize(w2, h2);
        }
        window.addEventListener('resize', onResize);
        VISOR._onResize = onResize;
    }

    function cerrarVisor360() {
        if (!VISOR.abierto) return;
        cancelAnimationFrame(VISOR.raf);
        if (VISOR.video) {
            VISOR.video.pause();
            VISOR.video.src = '';
            VISOR.video = null;
        }
        if (VISOR.texture) { VISOR.texture.dispose(); VISOR.texture = null; }
        if (VISOR.geometry) { VISOR.geometry.dispose(); VISOR.geometry = null; }
        if (VISOR.material) { VISOR.material.dispose(); VISOR.material = null; }
        if (VISOR.renderer) {
            VISOR.renderer.domElement.remove();
            VISOR.renderer.dispose();
            VISOR.renderer = null;
        }
        VISOR.scene = null;
        VISOR.camera = null;
        VISOR.sphere = null;
        // Quitar listeners
        if (VISOR._onResize) window.removeEventListener('resize', VISOR._onResize);
        var overlay = document.getElementById('visor-360');
        if (overlay) overlay.classList.remove('visible');
        VISOR.abierto = false;
        VISOR.yaw = 0;
        VISOR.pitch = 0;
    }

    // ── MOTOR DE AUTOPLAY (sonidos) ─────────────────────────────
    // Distribuye los audios de forma pareja a lo largo del fluir, dejando
    // 10s mudo al inicio y al final y un silencio corto entre audios.
    // Si la suma (audios + silencios) supera la duración del fluir, se
    // recortan audios para que nunca excedan ese total.

    // Pausa y limpia el elemento en reproducción.
    function detenerAudios() {
        if (SONIDO.elem) {
            try { SONIDO.elem.pause(); } catch (e) {}
            try { SONIDO.elem.src = ''; } catch (e) {}
            SONIDO.elem = null;
        }
        SONIDO.reproduciendo = false;
        SONIDO.actualPlan = null;
    }

    function limpiarSeleccionAudios() {
        detenerAudios();
        SONIDO.items = [];
        SONIDO.plan = [];
    }

    function limpiarSeleccionImagenes() {
        SLIDESHOW.items = [];
        SLIDESHOW.index = 0;
        SLIDESHOW.cont = null;
    }

    // Crea (o reutiliza) un <audio> oculto para reproducir.
    function obtenerElementoAudio() {
        if (!SONIDO.elem) {
            SONIDO.elem = new Audio();
            SONIDO.elem.preload = 'auto';
        }
        return SONIDO.elem;
    }

    // Reproduce el audio objetivo; se reanuda si es el mismo que ya sonaba
    // (sin reiniciar), o lo carga con el offset correcto si cambia de audio.
    function reproducirAudioObjetivo(objetivo) {
        if (!SONIDO.habilitado || !objetivo) { detenerAudios(); return; }

        if (SONIDO.actualPlan === objetivo) {
            return; // ya estamos intentando (o logrando) reproducir este tramo → no reiniciar
        }
        var url = 'api/servir_medio.php?id=' + objetivo.audio.id;

        var el = obtenerElementoAudio();
        el.src = url;
        el.currentTime = 0;
        SONIDO.actualPlan = objetivo;
        el.play().then(function() {
            SONIDO.reproduciendo = true;
        }).catch(function() {
            // autoplay bloqueado por el navegador → simplemente silencioso
            SONIDO.reproduciendo = false;
        });
    }

    // En cada frame del fluire, decide qué audio debe sonar según elapsedMs,
    // respetando los silencios de inicio/fin y el plan distribuido.
    function actualizarAudio(elapsedMs) {
        if (!SONIDO.habilitado) { detenerAudios(); return; }

        var tEn = elapsedMs - SONIDO_SILENCIO_INICIO;   // tiempo dentro de la ventana útil
        var fin = FLOW.duracionMs - SONIDO_SILENCIO_FINAL;
        if (tEn < 0 || tEn >= fin || !SONIDO.plan.length) {
            detenerAudios();
            return;
        }

        // Encontrar el audio cuyo tramo cubre tEn
        var objetivo = null;
        for (var i = 0; i < SONIDO.plan.length; i++) {
            var s = SONIDO.plan[i];
            if (tEn >= s.inicio && tEn < s.inicio + s.dur) { objetivo = s; break; }
        }
        if (!objetivo) {
            detenerAudios();
            return;
        }
        reproducirAudioObjetivo(objetivo);
    }

    // Construye el plan de reproducción desde la batería seleccionada.
    function planificarAudios() {
        var durTotal = FLOW.duracionMs;
        var util = durTotal - SONIDO_SILENCIO_INICIO - SONIDO_SILENCIO_FINAL;
        if (util < 0) util = 0;

        var plan = [];
        var acum = 0;

        // audios con duración conocida; si ninguno la tiene, usar 4s por defecto
        var disponibles = SONIDO.items.filter(function(i){ return i.duracion_seg; });
        if (!disponibles.length) disponibles = SONIDO.items;
        if (!disponibles.length) { SONIDO.plan = plan; return plan; }

        var numero = Math.min(disponibles.length, Math.max(1, Math.floor(util / 5000)));
        disponibles = disponibles.slice(0, numero);

        disponibles.forEach(function(a, idx) {
            var durA = a.duracion_seg ? a.duracion_seg * 1000 : 4000;
            // silencio entre audios (el primero no lleva silencio previo)
            if (idx > 0 && acum + SONIDO_SILENCIO_ENTRE <= util) {
                acum += SONIDO_SILENCIO_ENTRE;
            }
            // nunca superar el tiempo útil
            if (acum + durA > util) {
                if (acum >= util) return;
                durA = util - acum;
            }
            plan.push({ audio: a, inicio: acum, dur: durA });
            acum += durA;
        });
        SONIDO.plan = plan;
        return plan;
    }

    function iniciarFlow() {
        // Congelar las horas seleccionadas como el ciclo
        FLOW.horas = horasSeleccionadas.slice();
        if (FLOW.horas.length < 2) {
            // Si hay menos de 2 horas seleccionadas, usar todas
            FLOW.horas = HORAS.slice();
        }
        FLOW.inicio = Date.now();
        FLOW.activo = true;
        FLOW.ultimoSegundo = -1;
        FLOW.ultimoScroll = -1;
        // Resetear slideshow para que arranque desde el frame 0
        SLIDESHOW.ultimoAvance = 0;
        reiniciarSlideshow();
        actualizarBotonFluir();
        // Cargar medios filtrados (no bloquear el flow)
        cargarMediosFiltrados();
        // Re-filtrar el mapa con la nueva selección
        rerenderBloque('mapa');
        // Cargar mensajes Telegram si hay municipios seleccionados
        if (municipiosSeleccionados.length > 0) {
            cargarMensajesTelegram(municipiosSeleccionados.slice());
        } else {
            MENSAJES_TELEGRAM = null;
            renderComunicacionBlock();
        }
    }

    function detenerFlow() {
        FLOW.activo = false;
        detenerAudios();
        actualizarBotonFluir();
    }

    function actualizarFlow() {
        var elapsed = Date.now() - FLOW.inicio;
        if (elapsed >= FLOW.duracionMs) {
            // LOOP INFINITO: termina un ciclo de duracionMs y arranca otro.
            // Solo "Detener" lo corta.
            FLOW.inicio = Date.now();
            reiniciarSlideshow();      // vuelve a la imagen 0 + ultimoAvance = 0
            elapsed = 0;
        }

        // Autoplay de sonidos sincronizado con el tiempo del fluir
        actualizarAudio(elapsed);

        var horas = FLOW.horas;
        var progreso = elapsed / FLOW.duracionMs;            // 0..1
        var pos = progreso * horas.length;                   // 0..N
        var idx = Math.floor(pos) % horas.length;
        var t = pos - Math.floor(pos);

        var h1 = horas[idx];
        var h2 = horas[(idx + 1) % horas.length];

        // Siempre avanzar hacia el día (sentido horario), con wrapping de
        // medianoche. Ej 5→17 sube por 6,7,...; 17→5 continúa 18,19,...23,0,...5.
        // Se evita tomar el "camino corto" hacia atrás (17→16→...→5).
        var diff = h2 - h1;
        if (diff <= 0) diff += 24;   // cruzar medianoche y seguir hacia adelante
        var interpHour = h1 + diff * t;
        if (interpHour >= 24) interpHour -= 24;

        paletaTarget = interpolar(interpHour);

        // Scroll de Telegram durante el flow (sincronizado con la hora que corre)
        actualizarVentanaChat(interpHour);

        // Avance del slideshow de imágenes cada ~4s
        if (elapsed - SLIDESHOW.ultimoAvance > SLIDESHOW.intervaloMs) {
            avanzarSlideshow();
            SLIDESHOW.ultimoAvance = elapsed;
        }
    }

    function actualizarBotonFluir() {
        var btn = document.getElementById('btn-fluir');
        if (!btn) return;
        if (FLOW.activo) {
            btn.textContent = 'Detener';
            btn.classList.add('activo');
        } else {
            btn.textContent = 'Fluir';
            btn.classList.remove('activo');
        }
    }

    // ═══════════════════════════════════════════════════════
    //  GRID + DIBUJAR
    // ═══════════════════════════════════════════════════════

    function drawGrid() {
        if (worldBounds.w === 0) return;
        var spacing = Math.round(Math.max(worldBounds.w, worldBounds.h) / 14);
        spacing = Math.max(spacing, 20);

        var visLeft   = Math.max(-cam.tx / cam.scale, worldBounds.x);
        var visTop    = Math.max(-cam.ty / cam.scale, worldBounds.y);
        var visRight  = Math.min(visLeft + dims.w / cam.scale, worldBounds.x + worldBounds.w);
        var visBottom = Math.min(visTop  + dims.h / cam.scale, worldBounds.y + worldBounds.h);

        ctx.strokeStyle = rgb(paleta.accent);
        ctx.globalAlpha = 0.08;
        ctx.lineWidth = 1;
        ctx.beginPath();

        var startX = Math.floor(visLeft / spacing) * spacing;
        for (var x = startX; x <= visRight; x += spacing) {
            var sx = Math.round(x * cam.scale + cam.tx) + 0.5;
            var y0 = Math.round(visTop * cam.scale + cam.ty);
            var y1 = Math.round(visBottom * cam.scale + cam.ty);
            ctx.moveTo(sx, y0);
            ctx.lineTo(sx, y1);
        }
        var startY = Math.floor(visTop / spacing) * spacing;
        for (var y = startY; y <= visBottom; y += spacing) {
            var sy = Math.round(y * cam.scale + cam.ty) + 0.5;
            var x0 = Math.round(visLeft * cam.scale + cam.tx);
            var x1 = Math.round(visRight * cam.scale + cam.tx);
            ctx.moveTo(x0, sy);
            ctx.lineTo(x1, sy);
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    function dibujar() {
        // 1. Aplicar límites de cámara
        aplicarLimitesCamara();

        // 2. Fondo completo con el color de la paleta (toda la pantalla)
        ctx.fillStyle = rgb(paleta.bg);
        ctx.fillRect(0, 0, dims.w, dims.h);

        // 3. Borde sutil del mundo
        if (worldBounds.w > 0) {
            var sx = worldBounds.x * cam.scale + cam.tx;
            var sy = worldBounds.y * cam.scale + cam.ty;
            var sw = worldBounds.w * cam.scale;
            var sh = worldBounds.h * cam.scale;
            ctx.strokeStyle = 'rgba(255,255,255,.06)';
            ctx.lineWidth = 1;
            ctx.strokeRect(sx, sy, sw, sh);
        }

        // 4. Grid (limitada a worldBounds)
        drawGrid();

        // 5. Bloques HTML
        syncBlocks();

        // 6. Indicador de datos cargados
        if (DATOS_CARGADOS) {
            ctx.fillStyle = 'rgba(255,255,255,.08)';
            ctx.font = '10px Inter, system-ui, sans-serif';
            ctx.textAlign = 'right';
            ctx.fillText(DATOS_TOTAL + ' medios · ' + COLORES.length + ' colores · ' + PROVINCIAS.length + ' provincias', dims.w - 12, dims.h - 12);
        }
    }

    // ═══════════════════════════════════════════════════════
    //  TICK — transición suave de paleta (cada 16ms)
    // ═══════════════════════════════════════════════════════

    function tick() {
        // 1. Si el flow está activo, actualizar la paleta target
        if (FLOW.activo) {
            actualizarFlow();
        }

        // 2. Interpolación suave hacia el target
        var cambio = false;
        ['bg','text','accent','surface','slider'].forEach(function(k) {
            var a = paleta[k];
            var b = paletaTarget[k];
            if (a[0] !== b[0] || a[1] !== b[1] || a[2] !== b[2]) {
                paleta[k] = lerpColor(a, b, TASA);
                cambio = true;
            }
        });
        if (cambio) {
            dibujar();
            actualizarCSS();
        }
    }
    setInterval(tick, 16);

    // ═══════════════════════════════════════════════════════
    //  CSS vars + zoom controls
    // ═══════════════════════════════════════════════════════

    function actualizarCSS() {
        var t = paleta.text;
        var a = paleta.accent;
        document.documentElement.style.setProperty('--tr', t[0]);
        document.documentElement.style.setProperty('--tg', t[1]);
        document.documentElement.style.setProperty('--tb', t[2]);
        document.documentElement.style.setProperty('--ar', a[0]);
        document.documentElement.style.setProperty('--ag', a[1]);
        document.documentElement.style.setProperty('--ab', a[2]);
    }

    // ═══════════════════════════════════════════════════════
    //  RESIZE
    // ═══════════════════════════════════════════════════════

    function redimensionar() {
        dims.w = window.innerWidth - 90;
        dims.h = window.innerHeight;
        canvas.width = dims.w;
        canvas.height = dims.h;
        if (worldBounds.w === 0) {
            calcularBounds();
        }
        ajustarProporcionMundo();
        ajustarCamaraABloques();
        dibujar();
    }
    window.addEventListener('resize', redimensionar);

    // ═══════════════════════════════════════════════════════
    //  ZOOM + PAN
    // ═══════════════════════════════════════════════════════

    function actualizarZoomUI() {
        zoomSlider.min = cam.zoomMin;
        zoomSlider.max = cam.zoomMax;
        zoomSlider.value = cam.scale;
        zoomLabel.textContent = cam.scale.toFixed(1) + '\u00d7';
    }

    canvas.addEventListener('wheel', function(e) {
        e.preventDefault();
        var step = 0.06;
        cam.scale *= e.deltaY > 0 ? (1 - step) : (1 + step);
        cam.scale = Math.max(cam.zoomMin, Math.min(cam.zoomMax, cam.scale));
        actualizarZoomUI();
        dibujar();
    }, { passive: false });

    zoomSlider.addEventListener('input', function() {
        cam.scale = parseFloat(this.value);
        actualizarZoomUI();
        dibujar();
    });

    zoomInBtn.addEventListener('click', function() {
        cam.scale = Math.min(cam.zoomMax, cam.scale * 1.3);
        actualizarZoomUI();
        dibujar();
    });
    zoomOutBtn.addEventListener('click', function() {
        cam.scale = Math.max(cam.zoomMin, cam.scale / 1.3);
        actualizarZoomUI();
        dibujar();
    });

    canvas.addEventListener('mousedown', function(e) {
        drag.active = true;
        drag.lx = e.clientX;
        drag.ly = e.clientY;
        drag.ltx = cam.tx;
        drag.lty = cam.ty;
    });
    window.addEventListener('mousemove', function(e) {
        if (!drag.active) return;
        cam.tx = drag.ltx + (e.clientX - drag.lx);
        cam.ty = drag.lty + (e.clientY - drag.ly);
        dibujar();
    });
    window.addEventListener('mouseup', function() { drag.active = false; });

    canvas.addEventListener('dblclick', function() {
        ajustarCamaraABloques();
        dibujar();
    });

    // ═══════════════════════════════════════════════════════
    //  AJUSTAR CÁMARA A TODOS LOS BLOQUES
    // ═══════════════════════════════════════════════════════

    function ajustarCamaraABloques() {
        if (worldBounds.w === 0) return;
        var cx = worldBounds.x + worldBounds.w / 2;
        var cy = worldBounds.y + worldBounds.h / 2;
        var padding = 1.05;
        var sX = dims.w / (worldBounds.w * padding);
        var sY = dims.h / (worldBounds.h * padding);
        cam.zoomMin = Math.min(sX, sY);
        cam.zoomMax = 5;
        cam.scale = Math.max(cam.zoomMin, Math.min(cam.zoomMax, cam.scale));
        cam.tx = dims.w / 2 - cx * cam.scale;
        cam.ty = dims.h / 2 - cy * cam.scale;
        actualizarZoomUI();
    }

    // ═══════════════════════════════════════════════════════
    //  CARGAR DATOS DESDE API
    // ═══════════════════════════════════════════════════════

    function cargarDatos() {
        return fetch('api/recorrido.php')
            .then(function(r) { return r.json(); })
            .then(function(datos) {
                DATOS_CARGADOS = true;
                DATOS_API = datos;
                DATOS_TOTAL = datos.total;
                console.log('API datos cargados: ' + datos.total + ' medios, ' + (datos.colores||[]).length + ' colores');
                if (datos.colores && datos.colores.length) {
                    COLORES = datos.colores.map(function(c) {
                        return { nombre: c.nombre, hex: c.hex };
                    });
                }
                var provs = {};
                datos.puntos.forEach(function(p) {
                    if (p.provincia) provs[p.provincia] = true;
                });
                var provArr = Object.keys(provs).sort();
                if (provArr.length) {
                    PROVINCIAS = provArr.map(function(n) { return { nombre: n }; });
                }
                // Extraer municipios reales
                var munMap = {};
                datos.puntos.forEach(function(p) {
                    if (p.municipio) munMap[p.municipio] = true;
                });
                var munArr = Object.keys(munMap).sort();
                if (munArr.length) MUNICIPIOS = munArr;

                // Mapa municipio -> provincia (cada municipio pertenece a UNA
                // provincia; sin colisiones en los datos). Se usa al seleccionar
                // una provincia para marcar automáticamente sus municipios.
                MUNICIPIOS_POR_PROVINCIA = {};
                datos.puntos.forEach(function(p) {
                    if (p.municipio && p.provincia) {
                        MUNICIPIOS_POR_PROVINCIA[p.municipio] = p.provincia;
                    }
                });

                console.log('COLORES:', COLORES.map(function(c){return c.nombre;}).join(', '));
                console.log('PROVINCIAS:', PROVINCIAS.map(function(p){return p.nombre;}).join(', '));
                console.log('MUNICIPIOS:', MUNICIPIOS.join(', '));
                // Re-renderear bloques de selección con datos reales
                rerenderBloque('colores');
                rerenderBloque('provincias');
                rerenderBloque('municipios');
            })
            .then(function() {
                // Cargar tags reales desde la API
                return fetch('api/tags.php?limite=80')
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data && data.tags && data.tags.length) {
                            TAGS_API = data.tags;
                            rerenderBloque('tags');
                        }
                    })
                    .catch(function(e) {
                        console.warn('Error cargando tags', e);
                    });
            })
            .catch(function(e) {
                console.warn('API no disponible, usando datos hardcodeados', e);
            });
    }

    // ═══════════════════════════════════════════════════════
    //  RESUMEN EN SIDEBAR
    // ═══════════════════════════════════════════════════════

    function renderResumen() {
        var el = document.getElementById('sidebar-resumen');
        if (!el) return;
        if (!DATOS_API) {
            el.innerHTML = '<div style="opacity:.3">—</div>';
            return;
        }
        var d = DATOS_API;
        // Calcular rango de fechas
        var fechas = [];
        var tipos = {};
        d.puntos.forEach(function(p) {
            if (p.fecha) fechas.push(p.fecha);
            var t = p.tipo || 'otro';
            tipos[t] = (tipos[t] || 0) + 1;
        });
        fechas.sort();
        var rango = '';
        if (fechas.length) {
            var ini = fechas[0].slice(5);
            var fin = fechas[fechas.length - 1].slice(5);
            rango = ini + '-' + fin;
        }
        var provCnt = PROVINCIAS.length;
        var colCnt = COLORES.length;
        var tipoHtml = '';
        ['image','video','audio'].forEach(function(t) {
            var icono = {image:'img', video:'vid', audio:'aud'}[t] || t;
            if (tipos[t]) tipoHtml += '<div style="font-size:.55rem;opacity:.5">' + icono + ' <span class="num">' + tipos[t] + '</span></div>';
        });
        el.innerHTML = '<div><span class="num">' + d.total + '</span> medios</div>'
                     + tipoHtml
                     + '<div style="margin-top:.15rem"><span class="num">' + colCnt + '</span> col · <span class="num">' + provCnt + '</span> prov</div>'
                     + '<div style="font-size:.5rem;opacity:.35;margin-top:.1rem">' + rango + '</div>';
    }

    // ═══════════════════════════════════════════════════════
    //  INICIALIZAR (después de cargar datos)
    // ═══════════════════════════════════════════════════════

    function inicializar() {
    dims.w = window.innerWidth - 90;
    dims.h = window.innerHeight;
    canvas.width = dims.w;
    canvas.height = dims.h;

    // Construir BLOQUES desde la plantilla con escala y aleatoriedad
    var escala = dims.h / 1080;
    BLOQUES_TEMPLATE.forEach(function(t) {
        var fw = 0.7 + Math.random() * 0.6; // ±30%
        var fh = 0.7 + Math.random() * 0.6;
        if (t.id === 'tags') {
            // El cuadro de tags es denso (hasta 80): no permitir que se
            // encoja tanto que las etiquetas desborden sin scrollbar.
            fw = Math.max(fw, 0.85);
            fh = Math.max(fh, 0.85);
        }
        BLOQUES.push({
            id: t.id, tipo: t.tipo, titulo: t.titulo,
            w: Math.round(t.w * escala * fw),
            h: Math.round(t.h * escala * fh),
            mx: 0, my: 0
        });
    });

    colocarBloques();
    calcularBounds();
    ajustarProporcionMundo();
    ajustarCamaraABloques();
    dibujar();
    actualizarCSS();
    actualizarZoomUI();
    renderResumen();

    // Botón fluir
    document.getElementById('btn-fluir').addEventListener('click', function() {
        if (FLOW.activo) {
            detenerFlow();
        } else {
            iniciarFlowConFade();
        }
    });

    // Botón sonido: activa/desactiva el motor de audio (arranca apagado)
    function actualizarBotonSonido() {
        var btn = document.getElementById('btn-sonido');
        if (!btn) return;
        if (SONIDO.habilitado) {
            btn.textContent = '🔊 Sonido';
            btn.classList.add('activo');
        } else {
            btn.textContent = '🔇 Silencio';
            btn.classList.remove('activo');
        }
    }
    var btnSonido = document.getElementById('btn-sonido');
    if (btnSonido) {
        btnSonido.addEventListener('click', function() {
            SONIDO.habilitado = !SONIDO.habilitado;
            // Update audio element mute state without stopping playback
            if (SONIDO.elem) {
                SONIDO.elem.muted = !SONIDO.habilitado;
            }
            actualizarBotonSonido();
        });
        actualizarBotonSonido();
    }
    }

    // ── VISUALIZACIÓN ALEATORIA + FADE ──────────────────────────
    // Selección aleatoria de algunas horas y municipios al arrancar y
    // al presionar "Fluir", con un fade de pantalla hacia la nueva config.

    function aleatoriosDesde(arr, n) {
        var copia = arr.slice();
        var res = [];
        for (var i = 0; i < n && copia.length; i++) {
            res.push(copia.splice(Math.floor(Math.random() * copia.length), 1)[0]);
        }
        return res;
    }

    // Elige horas y municipios al azar y los refleja en los chips.
    function aplicarSeleccionAleatoria() {
        horasSeleccionadas = aleatoriosDesde(HORAS, 3 + Math.floor(Math.random() * 2)); // 3..4 horas
        if (horasSeleccionadas.length < 2) horasSeleccionadas = HORAS.slice(0, 2);
        var nMuni = 2 + Math.floor(Math.random() * 3); // 2..4 municipios
        municipiosSeleccionados = aleatoriosDesde(MUNICIPIOS, nMuni);
        // La hora actual maneja la paleta
        horaActual = horasSeleccionadas[Math.floor(Math.random() * horasSeleccionadas.length)];
        paletaTarget = interpolar(horaActual);
        // Reflejar en los chips selectores
        rerenderBloque('horas');
        rerenderBloque('municipios');
        // Cargar medios y mensajes según la nueva selección
        // (iniciarFlow se encarga de cargarlos al arrancar)
    }

    // Crea el overlay de fade si no existe.
    function obtenerOverlay() {
        var overlay = document.getElementById('fade-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'fade-overlay';
            document.body.appendChild(overlay);
        }
        overlay.style.background = 'rgb(' + paleta.bg.join(',') + ')';
        return overlay;
    }

    // Al presionar Fluir: arranca con la configuración actual del usuario
    var fadeEnCurso = false;
    function iniciarFlowConFade() {
        if (fadeEnCurso) return; // evitar doble click → doble fade
        fadeEnCurso = true;
        var overlay = obtenerOverlay();
        overlay.style.opacity = '1'; // fade out
        setTimeout(function() {
            fadeEnCurso = false;
            iniciarFlow();
            // fade in hacia la nueva configuración
            requestAnimationFrame(function() {
                overlay.style.opacity = '0';
            });
        }, 400);
    }

    // Arrancar: cargar datos de la API, inicializar y preseleccionar una
    // visualización aleatoria (algunas horas y municipios).
    cargarDatos().then(inicializar).then(function() { aplicarSeleccionAleatoria(); iniciarFlow(); });

})();
