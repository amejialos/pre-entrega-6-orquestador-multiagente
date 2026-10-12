---
doc_id: caudal-errores
titulo: Errores frecuentes y solución de problemas
categoria: troubleshooting
---

# Errores frecuentes y solución de problemas

## Códigos de error CAU

Todos los errores de Caudal tienen un código con el formato `CAU-NNN`. El primer dígito indica la familia: los `CAU-3xx` son errores de construcción del grafo, los `CAU-4xx` son errores de configuración o de conexión y los `CAU-5xx` son errores internos o de estado.

- `CAU-301`: hay una operación con estado (una ventana, `deduplicar` o un agregador) que no está precedida por `particionar_por`. Solución: agregá `particionar_por(clave)` antes de la operación.
- `CAU-401`: la fuente rechazó las credenciales. En Kafka suele ser un error en `sasl.username` o `sasl.password`; en PostgreSQL, un usuario sin permiso de replicación cuando se usa el modo `cdc`.
- `CAU-409`: ya hay un flujo con el mismo nombre corriendo sobre el mismo `CAUDAL_HOME`. Cambiá el nombre del flujo o apuntá a otro directorio.
- `CAU-417`: el esquema de un evento no coincide con el esquema registrado. Pasa cuando un productor cambia la estructura de los mensajes sin versionarla. Con `politica_esquema="tolerante"` los campos desconocidos se ignoran en lugar de fallar.
- `CAU-429`: el destino respondió que se superó el límite de pedidos. `HttpSumidero` reintenta automáticamente respetando el encabezado `Retry-After`.
- `CAU-503`: un worker murió de forma inesperada, normalmente por falta de memoria. Revisá el tamaño de las ventanas y el parámetro `tamano_lote`.
- `CAU-512`: el último checkpoint estaba corrupto y se usó el anterior. Es una advertencia, no un error fatal.

Cada excepción hereda de `CaudalError` y expone los atributos `codigo`, `recuperable` y `contexto`. El atributo `recuperable` indica si tiene sentido reintentar la operación: un `CAU-429` es recuperable, mientras que un `CAU-401` no lo es.

## Reintentos y cola de errores

Caudal distingue dos tipos de fallas: las transitorias, como una caída momentánea de la red o un `CAU-429`, y las permanentes, como un evento con datos inválidos. Para las transitorias se configura una política de reintento; para las permanentes se usa una cola de errores (dead letter queue).

La política por defecto es `ReintentoExponencial(intentos=5, base=0.5, maximo=30)`: el primer reintento espera medio segundo, el segundo un segundo, el tercero dos, y así sucesivamente hasta un máximo de treinta segundos entre intentos. A cada espera se le suma una variación aleatoria (jitter) para que muchos workers no reintenten todos al mismo tiempo. La política se asigna por operador con `mapear(funcion, reintento=ReintentoExponencial(intentos=3))` o globalmente en `CaudalConfig`.

Cuando un evento agota los reintentos, o cuando la excepción no es recuperable, Caudal no detiene el flujo: manda el evento a la cola de errores configurada con `flujo.cola_errores(sumidero)`. El evento se guarda junto con el código de error, el traceback y el nombre del operador que falló, para poder reprocesarlo después. Si no configurás una cola de errores, el comportamiento por defecto es detener el flujo, porque perder eventos en silencio es peor que frenar.

## Diagnóstico de problemas de rendimiento y memoria

El síntoma más común en producción es que el consumo de memoria crece sin parar hasta que el sistema operativo mata al worker y aparece un `CAU-503`. Las causas habituales son tres: ventanas de sesión con un tiempo de `inactividad` demasiado largo, `deduplicar` con un `ttl` enorme sobre claves de alta cardinalidad, y sumideros lentos que acumulan lotes pendientes.

Para confirmar cuál es la causa, activá las métricas con `CaudalConfig(metricas=True)`. Caudal expone un endpoint compatible con Prometheus en el puerto 9464 con métricas como `caudal_estado_bytes` (memoria usada por el estado de cada operador), `caudal_lotes_pendientes` y `caudal_latencia_segundos`. Si `caudal_estado_bytes` crece sin estabilizarse, el problema está en el estado; si lo que crece es `caudal_lotes_pendientes`, el cuello de botella es el sumidero.

Otro problema frecuente es que el flujo procesa cada evento dos veces después de un reinicio. No es un bug: es la semántica de "al menos una vez" descrita en la referencia de la API. Si tu sumidero no es idempotente, usá `deduplicar` antes de escribir o un sumidero transaccional. Para un análisis más profundo de dónde se va el tiempo de CPU, usá el perfilador `caudal-prof`, que se describe en la guía de rendimiento.
