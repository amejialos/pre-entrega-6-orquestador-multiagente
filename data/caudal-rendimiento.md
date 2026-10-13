---
doc_id: caudal-rendimiento
titulo: Guía de rendimiento y escalado
categoria: rendimiento
---

# Guía de rendimiento y escalado

## Paralelismo y workers

Caudal escala de dos maneras: verticalmente, agregando workers dentro de una misma máquina, y horizontalmente, corriendo el mismo flujo en varias máquinas que comparten el backend de estado `EstadoRedis`. Cada worker es un proceso separado del sistema operativo, no un hilo, para esquivar el bloqueo global del intérprete (GIL) de Python. Los eventos viajan entre procesos serializados con `msgpack`, que es más compacto y rápido que JSON.

La cantidad de workers se define con `CaudalConfig(workers=N)`. Una regla práctica: para cargas que usan mucha CPU (parseo, cálculos numéricos), poné tantos workers como núcleos físicos; para cargas dominadas por entrada y salida (llamadas HTTP, escrituras a base de datos), conviene menos workers con más `concurrencia` asíncrona dentro de cada uno, porque los procesos extra solo suman consumo de memoria.

El paralelismo real está limitado por la fuente. En Kafka, cada partición del tópico la lee un único worker, así que un tópico con cuatro particiones no se beneficia de más de cuatro workers de lectura. Si necesitás más paralelismo, aumentá las particiones del tópico o usá `particionar_por` después de la fuente para redistribuir el trabajo pesado entre más workers.

## Backpressure y tamaño de lote

El backpressure (contrapresión) es el mecanismo que evita que una fuente rápida inunde a un sumidero lento. En Caudal, cada operador tiene un búfer de entrada con capacidad limitada, controlada por `capacidad_bufer` (por defecto 10 lotes). Cuando el búfer de un operador se llena, el operador anterior se bloquea y la presión se propaga hacia atrás hasta la fuente, que deja de leer. En Kafka, eso significa que el consumidor simplemente pausa la lectura de las particiones hasta que haya lugar.

El tamaño de lote (`tamano_lote`) es la perilla principal para balancear latencia y throughput. Lotes grandes, de 5000 eventos o más, amortizan el costo de serialización y de las escrituras en red, y aumentan el throughput; a cambio, cada evento espera más hasta ser procesado. Lotes chicos, de 50 a 100 eventos, bajan la latencia pero aumentan la sobrecarga. Además del tamaño, existe `espera_maxima_lote` (por defecto 200 milisegundos): si el lote no se llena en ese tiempo, se procesa igual, para que en momentos de poco tráfico los eventos no queden esperando indefinidamente.

Como punto de partida, Caudal recomienda lotes de 500 eventos para pipelines de baja latencia y de 5000 para cargas batch o de ingesta masiva. Lo correcto es medir: cambiá un solo parámetro por vez y observá las métricas `caudal_latencia_segundos` y `caudal_eventos_procesados_total`.

## Perfilado con caudal-prof

`caudal-prof` es el perfilador incluido en el extra `caudal[completo]`. Se ejecuta envolviendo el script del pipeline: `caudal-prof ejecutar mi_pipeline.py --duracion 60`. Durante el tiempo indicado muestrea cada worker y al terminar genera un reporte HTML con un gráfico de llama (flame graph) por operador, el tiempo promedio por evento en cada etapa y el porcentaje de tiempo que cada operador pasó bloqueado por backpressure.

El reporte marca automáticamente como cuello de botella al operador con mayor tiempo por evento. Un operador que pasa mucho tiempo bloqueado no es el problema: el problema es el operador que viene después, que no consume lo suficientemente rápido. Esa distinción confunde a mucha gente la primera vez que lee un reporte.

Para no afectar el rendimiento en producción, `caudal-prof` usa muestreo estadístico con una frecuencia de 99 muestras por segundo, lo que agrega menos de un 2 % de sobrecarga. También se puede adjuntar a un flujo que ya está corriendo con `caudal-prof adjuntar --pid <PID>`, sin reiniciarlo. Los reportes se guardan en `CAUDAL_HOME/perfiles/` con la fecha y el nombre del flujo.
