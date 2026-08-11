<div align="center">

# Proxion

**Mensajería privada que de verdad es tuya.**

Chat, voz y vídeo con cifrado de extremo a extremo real, donde tus conversaciones viven en
un almacenamiento que tú controlas, no en los servidores de una empresa. Construido sobre el
estándar abierto [Solid](https://solidproject.org). Sin número de teléfono, sin registro, sin
ninguna empresa de por medio.

**Léelo en tu idioma:** [English](README.md) · Español · [Deutsch](README.de.md) · [Français](README.fr.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion en el escritorio: una conversación cifrada de extremo a extremo, con salas y contactos en la barra lateral" width="800">

</div>

## ¿Qué es Proxion?

Proxion es un mensajero como los que ya usas, con una diferencia que lo cambia todo: tus
datos te pertenecen.

Tus mensajes, archivos e historial de llamadas viven en tu propio **pod de Solid**, un
espacio de almacenamiento personal que tú controlas, en lugar de quedar encerrados dentro de
la app de una empresa. Elige un proveedor de pod gratuito, trae el tuyo o alójalo tú mismo, y
múdate cuando quieras. Tu identidad se crea en tu dispositivo, así que no hay ninguna cuenta
que registrar ni nada que se pueda filtrar.

Es un mensajero real, de todos los días: salas y mensajes directos, llamadas de voz y vídeo
con pantalla compartida, archivos, reacciones, respuestas y más, en Windows, macOS, Linux y
la web.

## Consigue Proxion

**Descárgalo y ábrelo.** No hay nada que configurar ni ningún servidor que ejecutar.

- **Windows, macOS o Linux:** ve a la [página de instalación](https://cafetechne.github.io/proxion-messenger/)
  o a la [última versión](../../releases/latest).
- **macOS con [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **En tu navegador:** Proxion también funciona como una app web instalable.

Como Proxion no está firmado por Apple ni por Microsoft (a propósito, para que ningún
guardián se interponga entre tú y tu propio software), tu sistema muestra un aviso único la
primera vez que lo abres. En Windows elige *Más información y luego Ejecutar de todas formas*;
en macOS *clic derecho y luego Abrir*; en Linux no hay ningún aviso.

## Qué puedes hacer

- **Enviar mensajes y llamar.** Salas de grupo y chats privados uno a uno, además de llamadas
  de voz y vídeo entre pares con pantalla compartida.
- **Conservar tu historial.** Todo vive en tu pod, en un formato abierto, así que es tuyo para
  guardarlo, leerlo con otras herramientas y llevártelo contigo.
- **Conversaciones de verdad privadas.** Los mensajes directos tienen cifrado de extremo a
  extremo, y puedes confirmar que de verdad hablas con tu contacto mediante una frase de
  seguridad corta que leéis en voz alta juntos. Las llamadas se cifran de la misma forma.
- **Llega a cualquiera en Solid.** Encuentra e invita a personas de todo el ecosistema Solid,
  no solo a otros usuarios de Proxion.
- **Úsalo en cualquier parte.** Escritorio, navegador y móvil, con capacidad sin conexión, en
  seis idiomas incluido el árabe de derecha a izquierda, y pensado para funcionar solo con
  lector de pantalla y teclado.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion en un teléfono" width="240">
</p>

## Parte del ecosistema Solid

Proxion es un buen ciudadano de Solid, no un jardín amurallado que simplemente usa Solid por
debajo. Una sala que creas se escribe en el formato de chat estándar de Solid, así que otras
apps de Solid pueden leerla y unirse a ella.

<img src="landing/assets/interop-sidebyside.png" alt="La misma sala mostrada lado a lado en Proxion y en el navegador de datos de SolidOS, con los mismos mensajes" width="900">

- **Abre una sala de Proxion en [SolidOS](https://solidos.org)** y cada mensaje está ahí. Esto
  se comprueba contra el SolidOS real en nuestras pruebas, no es solo una afirmación.
- **Encuentra e invita a personas por su WebID.** Descubre las salas que alguien aloja, o deja
  una invitación en su bandeja de entrada de Solid que cualquier app de Solid pueda leer.
- **Ve mensajes e invitaciones nuevos en tiempo real,** que te llegan incluso con Proxion
  cerrado.
- **Tus salas sobreviven a cualquier servidor.** La estructura de una sala vive en tu pod, así
  que puede reconstruirse solo a partir de tu pod.

Las salas compartidas son abiertas por diseño para que otras apps puedan leerlas; los mensajes
directos privados tienen cifrado de extremo a extremo y son legibles a propósito solo por
quienes participan en ellos. El formato de datos completo está documentado en
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), el panorama de compatibilidad app por app en
[docs/INTEROP.md](docs/INTEROP.md), y una auditoría requisito por requisito frente al conjunto
de especificaciones de Solid en [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Privado por diseño

- **Mensajes directos y llamadas con cifrado de extremo a extremo,** de modo que ningún relé
  ni servidor intermedio puede leerlos.
- **Tus datos en tu pod, a la vista.** Son datos documentados y estándar, no un bloque
  cerrado, así que cualquier app que autorices puede leerlos y puedes marcharte cuando quieras.
- **Verificable, no solo prometido.** Cada descarga puede rastrearse hasta este código fuente
  público, y miles de pruebas automatizadas se ejecutan con cada cambio.

Para los detalles, incluido el modelo de seguridad de las llamadas, el modelo de amenazas y
cómo verificar una descarga, consulta [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md),
[docs/CALLS.md](docs/CALLS.md), [SECURITY.md](SECURITY.md) y
[docs/VERIFYING.md](docs/VERIFYING.md).

## Contribuir

Proxion es de código abierto y las contribuciones son de verdad bienvenidas, desde informes de
errores hasta código. Empieza por [CONTRIBUTING.md](CONTRIBUTING.md). Si vienes de la comunidad
Solid y algo no interopera como esperas, ese es justo el tipo de incidencia que queremos
conocer.

## Para desarrolladores y quienes se autoalojan

La mayoría de la gente solo debería usar el instalador de arriba. Para trastear con Proxion o
ejecutar tu propia pasarela siempre activa (por ejemplo, para apuntar un teléfono a tu
escritorio):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # credenciales de pod opcionales; déjalo en blanco para uso solo local
python run_gateway.py
# abre http://localhost:8080
```

Construye un instalador nativo:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # empaqueta la pasarela para tu plataforma
cd tauri-app && cargo tauri build # instalador nativo
```

Ejecuta las pruebas:

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**Cómo encaja todo.** El frontend (en `web/`) lo sirve una pequeña **pasarela** (en
`proxion-messenger-core/`) que guarda tus claves, habla con tu pod y se conecta directamente
con las pasarelas de tus contactos. En el escritorio la pasarela va incluida dentro de la app y
arranca con ella, así que nunca la ves ni instalas Python. La pasarela existe porque Solid
cubre los datos y la identidad pero no la entrega en vivo, la presencia ni el establecimiento
de llamadas, el mismo papel que cumple un homeserver para Matrix o un servidor SMTP para el
correo. Detalles en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) y
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Licencia

[AGPL-3.0](LICENSE). Libre para usar, autoalojar, bifurcar y contribuir. Si ejecutas un Proxion
modificado como servicio para otros, tienes que publicar tus cambios. Ese es el sentido: nadie
puede volver a convertir esto en un silo.
