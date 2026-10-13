---
doc_id: caudal-seguridad
titulo: Seguridad y manejo de secretos
categoria: seguridad
---

# Seguridad y manejo de secretos

## Secretos con Bóveda

Caudal nunca debería recibir contraseñas o tokens escritos en el código del pipeline. Para eso existe la integración con Bóveda, el gestor de secretos de Caudal, que resuelve referencias a secretos en el momento de arrancar el flujo. En lugar de escribir la contraseña, escribís una referencia con el formato `boveda://ruta/al/secreto`:

```python
fuente = KafkaFuente(
    servidores="broker:9092",
    topicos=["pagos"],
    grupo="conciliacion",
    config_extra={"sasl.password": "boveda://kafka/pagos/password"},
)
```

Bóveda soporta varios proveedores de secretos. El proveedor `entorno` lee variables de entorno y está pensado para desarrollo local. El proveedor `vault` se conecta a HashiCorp Vault usando el token de la variable `VAULT_TOKEN` o autenticación por rol de Kubernetes. El proveedor `aws` lee de AWS Secrets Manager. El proveedor activo se elige con `CaudalConfig(proveedor_secretos="vault")`.

Los secretos resueltos se guardan solamente en memoria y nunca se escriben en los checkpoints ni en los logs. Si un secreto rota mientras el flujo corre, Bóveda lo vuelve a leer cuando el conector reporta un error de autenticación (`CAU-401`) y reintenta una vez antes de fallar. Esa renovación automática se puede desactivar con `renovar_secretos=False`.

## Cifrado en tránsito y en reposo

Todas las conexiones de los conectores oficiales soportan TLS. En `KafkaFuente` se activa con `security.protocol=SSL` o `SASL_SSL`; en `ConectorPostgres`, con `sslmode="verify-full"`, que además de cifrar verifica que el certificado del servidor coincida con el nombre del host. Caudal emite una advertencia al arrancar si detecta una conexión sin cifrar a un host que no es local.

Los checkpoints pueden contener datos sensibles, porque guardan el estado de los operadores: por ejemplo, las claves que está deduplicando o los agregados parciales de una ventana. Por eso Caudal permite cifrarlos en reposo con `CaudalConfig(cifrar_estado=True, clave_estado="boveda://caudal/clave-estado")`. El cifrado usa AES-256-GCM y la clave debe tener 32 bytes. Si perdés la clave, los checkpoints existentes quedan ilegibles y el flujo tiene que arrancar sin estado.

Para la comunicación entre workers en distintas máquinas, que pasa por `EstadoRedis`, se recomienda usar Redis con TLS y autenticación por ACL, dando al usuario de Caudal permisos solamente sobre las claves con el prefijo `caudal:`.

## Auditoría y datos personales

Cuando un pipeline procesa datos personales, como correos o documentos de identidad, Caudal ofrece el operador `enmascarar(campos, metodo)`. El método `hash` reemplaza el valor por un hash SHA-256 con sal, lo que permite seguir agrupando por ese campo sin exponer el dato original. El método `parcial` deja visibles solo los últimos caracteres, por ejemplo `****1234`. El método `eliminar` borra el campo por completo.

Conviene enmascarar lo antes posible en el flujo, inmediatamente después de la fuente, para que los datos sensibles no lleguen a los checkpoints ni a la cola de errores. Caudal advierte con un mensaje en el log si un evento que pasa por la cola de errores contiene campos marcados como sensibles con `CaudalConfig(campos_sensibles=[...])`.

Para auditoría, la opción `auditoria=True` registra en un log separado cada arranque y detención de un flujo, quién lo ejecutó, la versión de Caudal y un resumen de la configuración con los secretos ocultos. Ese log se escribe en `CAUDAL_HOME/auditoria/` en formato JSON Lines y se puede enviar a un sistema externo con cualquier sumidero.
