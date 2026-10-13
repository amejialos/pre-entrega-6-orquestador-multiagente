---
doc_id: caudal-instalacion
titulo: Instalación y configuración de Caudal
categoria: guia
---

# Instalación y configuración de Caudal

## Requisitos e instalación

Caudal es una librería de Python para construir pipelines de procesamiento de flujos de datos en tiempo real. Está pensada para equipos que necesitan leer eventos de una fuente (una cola de mensajes, una base de datos o un bucket de archivos), transformarlos en memoria y escribirlos en un destino sin montar un clúster dedicado. Toda la lógica corre en un único proceso de Python y escala agregando workers.

Caudal requiere Python 3.11 o superior. Las versiones 3.9 y 3.10 dejaron de estar soportadas a partir de Caudal 3.0, porque el planificador interno usa `asyncio.TaskGroup`, que no existe en versiones anteriores del lenguaje. En sistemas operativos, la librería se prueba en Linux (glibc 2.31 o superior), macOS 13 o superior y Windows 11. En Alpine Linux funciona, pero hay que compilar la extensión `caudal-nucleo` desde el código fuente porque no se publican ruedas para musl.

La forma recomendada de instalarla es con `pip` dentro de un entorno virtual:

```bash
python -m venv .venv
source .venv/bin/activate
pip install caudal
```

El paquete base no trae conectores externos, para que la instalación sea liviana. Los conectores se instalan como extras: `pip install "caudal[kafka]"` agrega el soporte para Apache Kafka, `pip install "caudal[postgres]"` agrega el conector de PostgreSQL y `pip install "caudal[s3]"` agrega el sumidero de Amazon S3. Si querés todo de una vez, existe el extra `caudal[completo]`, que instala todos los conectores y el perfilador.

Para verificar que la instalación quedó bien, ejecutá `caudal diagnostico` en la terminal. El comando imprime la versión instalada, la versión de Python, los extras detectados y si la extensión nativa `caudal-nucleo` se cargó correctamente. Si la extensión nativa no se pudo cargar, Caudal sigue funcionando con una implementación en Python puro, pero entre tres y cinco veces más lenta en las operaciones de ventana.

## Configuración con CaudalConfig

Toda la configuración global vive en un objeto `CaudalConfig`. Lo podés construir en código o dejar que Caudal lo arme a partir de un archivo `caudal.toml` y de variables de entorno. El orden de prioridad es: primero los argumentos pasados en código, después las variables de entorno y por último el archivo `caudal.toml`. Si ninguna fuente define un valor, se usa el default documentado.

Los parámetros más usados son `workers` (cantidad de workers en paralelo, por defecto la cantidad de núcleos de la CPU), `tamano_lote` (cuántos eventos se procesan juntos, por defecto 500), `directorio_estado` (dónde se guardan los checkpoints) y `nivel_log` (por defecto `INFO`).

Las variables de entorno usan el prefijo `CAUDAL_`. La más importante es `CAUDAL_HOME`, que define el directorio raíz donde Caudal guarda los checkpoints, los logs y la caché de esquemas. Si no la definís, Caudal usa `~/.caudal`. En contenedores conviene apuntar `CAUDAL_HOME` a un volumen persistente; si no, cada reinicio del contenedor pierde los checkpoints y el pipeline vuelve a procesar los eventos desde el principio. Otras variables útiles son `CAUDAL_WORKERS`, `CAUDAL_TAMANO_LOTE` y `CAUDAL_NIVEL_LOG`.

Un ejemplo de configuración en código:

```python
from caudal import CaudalConfig, Flujo

config = CaudalConfig(workers=4, tamano_lote=1000, nivel_log="DEBUG")
flujo = Flujo("pedidos", config=config)
```

El método `CaudalConfig.desde_entorno()` construye la configuración leyendo solamente las variables de entorno, sin mirar el archivo `caudal.toml`. Es útil en despliegues donde la configuración se inyecta desde el orquestador.

## Primer pipeline de ejemplo

El ejemplo mínimo lee líneas de un archivo, las convierte a mayúsculas y las imprime. Sirve para comprobar que el entorno está listo antes de conectar fuentes reales.

```python
from caudal import Flujo
from caudal.fuentes import ArchivoFuente
from caudal.sumideros import ConsolaSumidero

flujo = (
    Flujo("demo")
    .desde(ArchivoFuente("eventos.txt"))
    .mapear(str.upper)
    .hacia(ConsolaSumidero())
)
flujo.ejecutar()
```

El método `ejecutar()` bloquea hasta que la fuente se agota. Para fuentes infinitas, como un tópico de Kafka, el flujo corre hasta que recibe una señal de interrupción (`SIGINT` o `SIGTERM`). Al recibirla, Caudal termina de procesar el lote en curso, guarda un checkpoint y recién después cierra los conectores. Ese cierre ordenado se llama apagado elegante y se puede desactivar con `ejecutar(apagado_elegante=False)`, aunque no es recomendable en producción.

Si preferís un estilo asíncrono, existe `await flujo.ejecutar_async()`, que permite correr varios flujos dentro del mismo event loop. Cada flujo tiene un nombre único; si dos flujos comparten nombre dentro del mismo `CAUDAL_HOME`, el segundo falla al arrancar con el error `CAU-409` para evitar que pisen sus checkpoints.
