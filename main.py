import os
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="SwipeJob Universal Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "https://swipejob-n8n.onrender.com/webhook/webhook/swipe")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# MODELOS DE DATOS
class RegistroModel(BaseModel):
    nombre: str
    correo: str
    password: str
    rol: str
    telefono: Optional[str] = None

class LoginModel(BaseModel):
    correo: str
    password: str

class PerfilModel(BaseModel):
    id_candidato: int
    nombre: str
    telefono: Optional[str] = None
    estudios: Optional[str] = None
    proyeccion: Optional[str] = None
    perfil_profesional: Optional[str] = None

class PreferenciasModel(BaseModel):
    id_candidato: int
    cargo_deseado: str
    ubicacion_deseada: str
    empresas_interes: str
    rango_salarial: str
    modalidad: str
    tipo_contrato: str

class VacanteModel(BaseModel):
    id_empresa: int
    titulo: str
    descripcion: str
    requisitos: str
    salario: str
    modalidad: str
    ubicacion: Optional[str] = "Colombia"
    tipo_contrato: Optional[str] = "Término indefinido"

class SwipeModel(BaseModel):
    id_candidato: int
    id_vacante: int
    decision: str
    afinidad: Optional[int] = 0

class EntrevistaModel(BaseModel):
    id_candidato: int
    id_empresa: int
    id_postulacion: Optional[int] = None
    titulo: str
    fecha_hora: str
    modalidad: Optional[str] = "Remoto"
    enlace_reunion: Optional[str] = None
    notas: Optional[str] = None

# AUTENTICACIÓN DINÁMICA
@app.post("/api/registro")
def registro(data: RegistroModel):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id_usuario FROM usuarios WHERE correo = %s", (data.correo,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="El correo ya se encuentra registrado.")

        cur.execute(
            """
            INSERT INTO usuarios (nombre, correo, password, rol, telefono)
            VALUES (%s, %s, %s, %s, %s) RETURNING id_usuario
            """,
            (data.nombre, data.correo, data.password, data.rol, data.telefono)
        )
        id_usuario = cur.fetchone()["id_usuario"]
        id_candidato = None
        id_empresa = None

        if data.rol == "candidato":
            cur.execute("INSERT INTO candidatos (id_usuario) VALUES (%s) RETURNING id_candidato", (id_usuario,))
            id_candidato = cur.fetchone()["id_candidato"]
        else:
            cur.execute("INSERT INTO empresas (id_usuario, nombre) VALUES (%s, %s) RETURNING id_empresa", (id_usuario, data.nombre))
            id_empresa = cur.fetchone()["id_empresa"]

        conn.commit()
        return {
            "id_usuario": id_usuario,
            "nombre": data.nombre,
            "correo": data.correo,
            "rol": data.rol,
            "id_candidato": id_candidato,
            "id_empresa": id_empresa
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/api/login")
def login(data: LoginModel):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM usuarios WHERE correo = %s AND password = %s", (data.correo, data.password))
        user = cur.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

        id_candidato = None
        id_empresa = None

        if user["rol"] == "candidato":
            cur.execute("SELECT id_candidato FROM candidatos WHERE id_usuario = %s", (user["id_usuario"],))
            cand = cur.fetchone()
            if cand:
                id_candidato = cand["id_candidato"]
        else:
            cur.execute("SELECT id_empresa FROM empresas WHERE id_usuario = %s", (user["id_usuario"],))
            emp = cur.fetchone()
            if emp:
                id_empresa = emp["id_empresa"]

        return {
            "id_usuario": user["id_usuario"],
            "nombre": user["nombre"],
            "correo": user["correo"],
            "rol": user["rol"],
            "id_candidato": id_candidato,
            "id_empresa": id_empresa
        }
    finally:
        cur.close()
        conn.close()

# PERFILES Y HOJA DE VIDA
@app.get("/api/candidato/perfil/{id_candidato}")
def obtener_perfil(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT c.id_candidato, u.nombre, u.correo, u.telefono, c.estudios, c.proyeccion, c.perfil_profesional, c.cv_nombre
            FROM candidatos c
            JOIN usuarios u ON c.id_usuario = u.id_usuario
            WHERE c.id_candidato = %s
            """,
            (id_candidato,)
        )
        return cur.fetchone() or {}
    finally:
        cur.close()
        conn.close()

@app.put("/api/candidato/perfil")
def actualizar_perfil(data: PerfilModel):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE candidatos 
            SET estudios = %s, proyeccion = %s, perfil_profesional = %s 
            WHERE id_candidato = %s RETURNING id_usuario
            """,
            (data.estudios, data.proyeccion, data.perfil_profesional, data.id_candidato)
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE usuarios SET nombre = %s, telefono = %s WHERE id_usuario = %s",
                (data.nombre, data.telefono, row["id_usuario"])
            )
        conn.commit()
        return {"mensaje": "Perfil actualizado exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/api/candidato/cv")
async def subir_cv(id_candidato: int = Form(...), archivo: UploadFile = File(...)):
    conn = get_db()
    cur = conn.cursor()
    try:
        contenido = await archivo.read()
        texto = contenido.decode("utf-8", errors="ignore")

        cur.execute(
            """
            INSERT INTO hojas_vida (id_candidato, nombre_archivo, contenido_texto)
            VALUES (%s, %s, %s)
            """,
            (id_candidato, archivo.filename, texto)
        )
        cur.execute("UPDATE candidatos SET cv_nombre = %s WHERE id_candidato = %s", (archivo.filename, id_candidato))
        conn.commit()
        return {"mensaje": f"Archivo {archivo.filename} cargado exitosamente."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/api/candidato/preferencias")
def guardar_preferencias(data: PreferenciasModel):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO preferencias_candidato (id_candidato, cargo_deseado, ubicacion_deseada, empresas_interes, rango_salarial, modalidad, tipo_contrato)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (data.id_candidato, data.cargo_deseado, data.ubicacion_deseada, data.empresas_interes, data.rango_salarial, data.modalidad, data.tipo_contrato)
        )
        conn.commit()
        return {"mensaje": "Preferencias registradas exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# VACANTES
@app.get("/api/vacantes/feed/{id_candidato}")
def feed_vacantes(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT cv_nombre FROM candidatos WHERE id_candidato = %s", (id_candidato,))
        cand = cur.fetchone()
        if not cand or not cand["cv_nombre"]:
            return {"requiere_cv": True, "vacantes": []}

        cur.execute(
            """
            SELECT v.id_vacante, v.titulo, v.descripcion, v.requisitos, v.salario, v.modalidad, v.ubicacion, e.nombre as nombre_empresa
            FROM vacantes v
            JOIN empresas e ON v.id_empresa = e.id_empresa
            WHERE v.id_vacante NOT IN (
                SELECT id_vacante FROM matches WHERE id_candidato = %s
            )
            ORDER BY v.id_vacante DESC
            """,
            (id_candidato,)
        )
        return {"requiere_cv": False, "vacantes": cur.fetchall()}
    finally:
        cur.close()
        conn.close()

@app.post("/api/vacantes")
def crear_vacante(data: VacanteModel):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO vacantes (id_empresa, titulo, descripcion, requisitos, salario, modalidad, ubicacion, tipo_contrato)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id_vacante
            """,
            (data.id_empresa, data.titulo, data.descripcion, data.requisitos, data.salario, data.modalidad, data.ubicacion, data.tipo_contrato)
        )
        conn.commit()
        return {"mensaje": "Vacante publicada exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# SWIPES Y DISPARO UNIVERSAL A N8N
@app.post("/api/swipes")
def registrar_swipe(data: SwipeModel, background_tasks: BackgroundTasks):
    conn = get_db()
    cur = conn.cursor()
    afinidad_segura = data.afinidad if data.afinidad is not None else 0

    try:
        # Registrar en matches
        cur.execute(
            """
            INSERT INTO matches (id_candidato, id_vacante, decision, afinidad)
            VALUES (%s, %s, %s, %s)
            """,
            (data.id_candidato, data.id_vacante, data.decision, afinidad_segura)
        )

        if data.decision == "like":
            # Registrar en postulaciones
            cur.execute(
                """
                INSERT INTO postulaciones (id_candidato, id_vacante, estado, afinidad)
                VALUES (%s, %s, 'Enviada', %s)
                """,
                (data.id_candidato, data.id_vacante, afinidad_segura)
            )

            # Notificaciones In-App para ambos usuarios
            cur.execute("SELECT id_usuario FROM candidatos WHERE id_candidato = %s", (data.id_candidato,))
            cand_user = cur.fetchone()

            cur.execute(
                """
                SELECT e.id_usuario 
                FROM vacantes v 
                JOIN empresas e ON v.id_empresa = e.id_empresa 
                WHERE v.id_vacante = %s
                """,
                (data.id_vacante,)
            )
            emp_user = cur.fetchone()

            if cand_user:
                cur.execute(
                    "INSERT INTO notificaciones (id_usuario, tipo, titulo, mensaje) VALUES (%s, 'match', 'Postulación Enviada', 'Tu postulación se ha registrado exitosamente.')",
                    (cand_user["id_usuario"],)
                )
            if emp_user:
                cur.execute(
                    "INSERT INTO notificaciones (id_usuario, tipo, titulo, mensaje) VALUES (%s, 'postulacion', 'Nuevo Postulante', 'Un candidato ha aplicado a una de tus vacantes activas.')",
                    (emp_user["id_usuario"],)
                )

            conn.commit()

            # Despacho en segundo plano a n8n únicamente con los IDs de la operación
            def emitir_n8n():
                try:
                    payload = {
                        "evento": "nuevo_swipe_like",
                        "id_candidato": data.id_candidato,
                        "id_vacante": data.id_vacante,
                        "afinidad": afinidad_segura
                    }
                    requests.post(N8N_WEBHOOK_URL, json=payload, timeout=6)
                except Exception as err:
                    print(f"Error conectando con n8n: {err}")

            background_tasks.add_task(emitir_n8n)
        else:
            conn.commit()

        return {"status": "ok", "mensaje": "Swipe procesado"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

# HISTORIAL DE POSTULACIONES
@app.get("/api/candidato/postulaciones/{id_candidato}")
def historial_candidato(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.id_postulacion, v.titulo, e.nombre as empresa, p.estado, p.afinidad, p.fecha_postulacion
            FROM postulaciones p
            JOIN vacantes v ON p.id_vacante = v.id_vacante
            JOIN empresas e ON v.id_empresa = e.id_empresa
            WHERE p.id_candidato = %s
            ORDER BY p.id_postulacion DESC
            """,
            (id_candidato,)
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()

@app.get("/api/empresa/postulaciones/{id_empresa}")
def postulaciones_empresa(id_empresa: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.id_postulacion, p.id_candidato, u.nombre as candidato_nombre, v.titulo as vacante_titulo, c.estudios, p.afinidad, p.estado
            FROM postulaciones p
            JOIN vacantes v ON p.id_vacante = v.id_vacante
            JOIN candidatos c ON p.id_candidato = c.id_candidato
            JOIN usuarios u ON c.id_usuario = u.id_usuario
            WHERE v.id_empresa = %s
            ORDER BY p.id_postulacion DESC
            """,
            (id_empresa,)
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()

# ENTREVISTAS
@app.post("/api/empresa/entrevistas")
def programar_entrevista(data: EntrevistaModel, background_tasks: BackgroundTasks):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO entrevistas (id_candidato, id_empresa, id_postulacion, titulo, fecha_hora, modalidad, enlace_reunion, notas, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'programada') RETURNING id_entrevista
            """,
            (data.id_candidato, data.id_empresa, data.id_postulacion, data.titulo, data.fecha_hora, data.modalidad, data.enlace_reunion, data.notas)
        )
        id_entrevista = cur.fetchone()["id_entrevista"]
        conn.commit()

        def emitir_cita_n8n():
            try:
                requests.post(N8N_WEBHOOK_URL, json={
                    "evento": "entrevista_agendada",
                    "id_candidato": data.id_candidato,
                    "id_empresa": data.id_empresa,
                    "titulo": data.titulo,
                    "fecha_hora": data.fecha_hora,
                    "enlace_reunion": data.enlace_reunion
                }, timeout=6)
            except Exception as err:
                print(f"Error despachando cita a n8n: {err}")

        background_tasks.add_task(emitir_cita_n8n)
        return {"id_entrevista": id_entrevista, "mensaje": "Entrevista agendada exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.get("/api/candidato/entrevistas/{id_candidato}")
def entrevistas_candidato(id_candidato: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT ent.id_entrevista, ent.titulo, e.nombre as empresa_nombre, ent.fecha_hora, ent.duracion_minutos, ent.modalidad, ent.enlace_reunion, ent.estado
            FROM entrevistas ent
            JOIN empresas e ON ent.id_empresa = e.id_empresa
            WHERE ent.id_candidato = %s
            ORDER BY ent.fecha_hora ASC
            """,
            (id_candidato,)
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()

@app.get("/api/empresa/entrevistas/{id_empresa}")
def entrevistas_empresa(id_empresa: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT ent.id_entrevista, ent.titulo, u.nombre as candidato_nombre, ent.fecha_hora, ent.duracion_minutos, ent.modalidad, ent.enlace_reunion, ent.estado
            FROM entrevistas ent
            JOIN candidatos c ON ent.id_candidato = c.id_candidato
            JOIN usuarios u ON c.id_usuario = u.id_usuario
            WHERE ent.id_empresa = %s
            ORDER BY ent.fecha_hora ASC
            """,
            (id_empresa,)
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()

@app.put("/api/entrevistas/{id_entrevista}/confirmar")
def confirmar_entrevista(id_entrevista: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE entrevistas SET estado = 'confirmada' WHERE id_entrevista = %s", (id_entrevista,))
        conn.commit()
        return {"mensaje": "Entrevista confirmada"}
    finally:
        cur.close()
        conn.close()

# NOTIFICACIONES
@app.get("/api/candidato/notificaciones/{id_usuario}")
def listar_notificaciones(id_usuario: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM notificaciones WHERE id_usuario = %s ORDER BY id_notificacion DESC", (id_usuario,))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()

@app.put("/api/notificaciones/{id_notificacion}/leer")
def leer_notificacion(id_notificacion: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE notificaciones SET leida = TRUE WHERE id_notificacion = %s", (id_notificacion,))
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()

@app.put("/api/notificaciones/usuario/{id_usuario}/leer-todas")
def leer_todas_notificaciones(id_usuario: int):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE notificaciones SET leida = TRUE WHERE id_usuario = %s", (id_usuario,))
        conn.commit()
        return {"status": "ok"}
    finally:
        cur.close()
        conn.close()
