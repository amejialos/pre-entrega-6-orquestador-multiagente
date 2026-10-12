---
doc_id: caudal-conectores
titulo: Conectores de fuentes y sumideros
categoria: integraciones
---

# Conectores de fuentes y sumideros

## KafkaFuente: leer desde Apache Kafka

`KafkaFuente` consume mensajes de uno o más tópicos de Apache Kafka. Se instala con el extra `caudal[kafka]` y por debajo usa la librería `confluent-kafka`. Los parámetros obligatorios son `servidores` (la lista de brokers, por ejemplo `"broker1:9092,broker2:9092"`), `topicos` y `grupo`, que es el identificador del grupo de consumidores.

```python
from caudal.fuentes import KafkaFuente

fuente = KafkaFuente(
    servidores="localhost:9092",
    topicos=["pedidos"],
    grupo="facturacion",
    desde="ultimo",
)
```

El parámetro `desde` define dónde empezar cuando el grupo todavía no tiene offsets guardados: `"ultimo"` lee solo mensajes nuevos y `"primero"` reprocesa el tópico desde el principio. Una vez que existe un checkpoint, ese parámetro se ignora y la lectura retoma desde el offset guardado.

Caudal no confirma (commit) los offsets en Kafka después de cada mensaje. Los confirma solamente cuando se completa un checkpoint, para que los offsets y el estado de los operadores queden siempre alineados. Por eso conviene desactivar el autocommit; `KafkaFuente` lo hace automáticamente y emite una advertencia si detecta `enable.auto.commit=true` en la configuración extra.

Para clústeres con autenticación, los parámetros de `librdkafka` se pasan en el diccionario `config_extra`, por ejemplo `{"security.protocol": "SASL_SSL", "sasl.mechanism": "SCRAM-SHA-512"}`. Los mensajes se deserializan por defecto como JSON; con `deserializador=AvroDeserializador(registro_url)` se pueden leer mensajes en Avro usando un Schema Registry.

## ConectorPostgres: fuente y sumidero de base de datos

`ConectorPostgres` funciona como fuente y como sumidero. Se instala con `caudal[postgres]` y usa `psycopg` 3 por debajo. Como fuente tiene dos modos. El modo `consulta` ejecuta periódicamente una consulta incremental sobre una columna creciente (un identificador o una marca de tiempo) y emite las filas nuevas. El modo `cdc` (captura de cambios) lee el log de replicación lógica de PostgreSQL usando el plugin `pgoutput` y emite cada inserción, actualización y borrado como un evento con el campo `operacion`.

El modo `cdc` requiere configurar `wal_level = logical` en el servidor y crear una publicación (`CREATE PUBLICATION`) para las tablas que querés seguir. Caudal crea el slot de replicación automáticamente con el nombre `caudal_<nombre_flujo>`. Si borrás un flujo, acordate de eliminar el slot con `SELECT pg_drop_replication_slot(...)`; un slot abandonado impide que PostgreSQL recicle los archivos WAL y puede llenar el disco del servidor.

Como sumidero, `ConectorPostgres` inserta los eventos en lotes usando el protocolo `COPY`, que es mucho más rápido que `INSERT` fila por fila. Con `modo="upsert"` y `clave_conflicto=["id"]` actualiza las filas existentes en lugar de fallar. Con `transaccional=True` escribe cada lote dentro de una transacción que se confirma junto con el checkpoint, lo que da semántica de "exactamente una vez" de punta a punta.

## S3Sumidero y otros destinos

`S3Sumidero` escribe eventos en Amazon S3 o en cualquier almacenamiento compatible con la API de S3, como MinIO. Se instala con `caudal[s3]`. Agrupa eventos en archivos y los sube cuando se cumple la primera de dos condiciones: el archivo alcanza `tamano_maximo` (por defecto 128 MB) o pasa el tiempo `rotacion` (por defecto 5 minutos).

Los formatos soportados son `jsonl`, `csv` y `parquet`. Para análisis posterior con herramientas como DuckDB o Athena se recomienda Parquet con compresión `zstd`. El parámetro `particion` acepta una plantilla como `"anio={anio}/mes={mes}/dia={dia}"`, que genera rutas compatibles con el particionado estilo Hive.

Las credenciales se toman de la cadena estándar de AWS: variables de entorno, archivo `~/.aws/credentials` o el rol de la instancia. Nunca se pasan como argumento en texto plano; para eso está la integración con Bóveda que se describe en la guía de seguridad.

Además de los tres conectores principales, Caudal incluye `ConsolaSumidero` para depurar, `ArchivoFuente` y `ArchivoSumidero` para archivos locales, `HttpSumidero` para enviar eventos a un webhook con reintentos y `MemoriaSumidero`, que acumula eventos en una lista y está pensado para los tests unitarios de tus pipelines.
