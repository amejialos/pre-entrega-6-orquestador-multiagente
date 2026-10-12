---
doc_id: caudal-api-flujos
titulo: Referencia de la API de flujos
categoria: referencia-api
---

# Referencia de la API de flujos

## La clase Flujo y sus operadores

`Flujo` es la clase central de Caudal. Representa un grafo dirigido de operaciones que empieza en una fuente, pasa por cero o más operadores y termina en uno o más sumideros. Los operadores no modifican el flujo original: cada llamada devuelve un flujo nuevo, lo que permite encadenarlos con estilo fluido y reutilizar ramas.

Los operadores básicos son:

- `mapear(funcion)`: aplica una función a cada evento y emite el resultado. Es el equivalente a `map`.
- `filtrar(predicado)`: deja pasar solamente los eventos para los que el predicado devuelve `True`.
- `aplanar()`: si un evento es una lista, emite cada elemento como un evento separado.
- `particionar_por(clave)`: reparte los eventos entre workers según una clave, garantizando que todos los eventos con la misma clave los procese el mismo worker. Es obligatorio antes de cualquier operación con estado.
- `deduplicar(clave, ttl)`: descarta eventos repetidos con la misma clave dentro de una ventana de tiempo `ttl`.
- `ramificar(*predicados)`: divide el flujo en varias ramas según condiciones, útil para mandar eventos inválidos a una cola de errores.

Las funciones que se pasan a `mapear` y `filtrar` pueden ser síncronas o asíncronas. Si son corrutinas, Caudal las ejecuta concurrentemente dentro de cada lote, con un límite configurable por el parámetro `concurrencia` (por defecto 32). Es la forma recomendada de llamar a APIs HTTP externas desde un flujo sin bloquear al worker.

Un detalle importante: los operadores son perezosos. Nada se ejecuta hasta llamar a `ejecutar()` o `ejecutar_async()`. Antes de ejecutar, Caudal valida el grafo completo y falla con el error `CAU-301` si encuentra una operación con estado que no está precedida por `particionar_por`.

## Ventanas y agregaciones

Las ventanas agrupan eventos en intervalos de tiempo para calcular agregados como conteos, sumas o promedios. Caudal ofrece tres tipos:

- `ventana_fija(duracion)`: intervalos consecutivos sin solapamiento, por ejemplo uno por minuto. También se la conoce como ventana tumbling.
- `ventana_deslizante(duracion, paso)`: intervalos que se solapan; con `duracion=60` y `paso=10`, cada diez segundos se emite el agregado del último minuto.
- `ventana_sesion(inactividad)`: agrupa eventos de la misma clave hasta que pasa un tiempo `inactividad` sin eventos nuevos. Es útil para medir sesiones de usuarios en un sitio web.

Después de definir la ventana se llama a `agregar()` con una función de agregación. Caudal trae agregadores incorporados en `caudal.agregadores`: `Conteo`, `Suma`, `Promedio`, `Maximo`, `Minimo` y `Percentil`. También podés escribir el tuyo heredando de `Agregador` e implementando `inicial()`, `acumular()` y `combinar()`.

El tiempo de cada evento se toma por defecto del momento en que llega (tiempo de procesamiento). Para usar el tiempo en que ocurrió el evento (tiempo de evento), pasá `marca_tiempo=lambda e: e["ts"]` al crear la ventana. Con tiempo de evento aparecen los eventos tardíos: Caudal los acepta hasta un margen definido por `tolerancia_retraso` (por defecto 5 segundos). Los que llegan después de ese margen se descartan y se cuentan en la métrica `caudal_eventos_tardios_total`. Esa tolerancia se implementa con marcas de agua, que avanzan a medida que llegan eventos con marcas de tiempo más nuevas.

## Estado y checkpoints

Los operadores con estado, como ventanas, `deduplicar` y los agregadores, guardan información entre eventos. Ese estado vive en memoria y Caudal lo persiste periódicamente en un checkpoint dentro de `CAUDAL_HOME/estado/<nombre_flujo>/`. El intervalo se configura con `intervalo_checkpoint`, que por defecto es de 30 segundos.

Cuando un flujo se reinicia, Caudal busca el último checkpoint válido, restaura el estado de cada operador y le pide a la fuente que retome desde la posición guardada (el offset en Kafka, el último identificador leído en PostgreSQL). Gracias a eso, el procesamiento tiene semántica de "al menos una vez": ningún evento se pierde, pero algunos pueden procesarse dos veces si el corte ocurrió entre dos checkpoints. Para lograr "exactamente una vez" hay que combinar los checkpoints con un sumidero transaccional, como `ConectorPostgres` en modo `transaccional=True`.

El almacenamiento del estado es intercambiable. El backend por defecto es `EstadoLocal`, que escribe archivos en disco. Para despliegues con varios nodos existe `EstadoRedis`, que guarda el estado en Redis y permite que otro nodo retome un flujo caído. Los checkpoints corruptos se detectan con una suma de verificación; si el último está dañado, Caudal usa el anterior y registra una advertencia `CAU-512`.
