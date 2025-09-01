# bot.py
# Este bot de Telegram es un planificador financiero personal.
# Requiere las siguientes librerías:
# pip install python-telegram-bot firebase-admin

import logging
import json
import os
from datetime import datetime
from decimal import Decimal, getcontext, InvalidOperation
import random

# --- Firebase Admin SDK ---
import firebase_admin
from firebase_admin import credentials, firestore

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ============================================================================ #
# SECTION 0: CONFIGURACIÓN INICIAL Y CONSTANTES
# ============================================================================ #

# --- COMPLETA TUS CREDENCIALES AQUÍ ---
# La configuración del token de Telegram debe provenir de variables de entorno para evitar
# exponer credenciales sensibles en el código fuente.  Utilizamos python‑dotenv en
# entornos de desarrollo para cargar automáticamente valores desde un archivo `.env` si
# está presente.  En producción, asegúrate de definir la variable de entorno
# `TELEGRAM_TOKEN` en tu hosting o entorno de ejecución.

# Cargar .env de manera opcional en desarrollo.  Si la librería no está instalada
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    # No hacemos nada si dotenv no está disponible
    pass

# 1. token de bot de Telegram.  Nunca hardcodees el token aquí; utiliza la variable
#    de entorno TELEGRAM_TOKEN.  Si la variable no existe, TELEGRAM_TOKEN será None.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# 2. Asegúrate de que el archivo de credenciales de Firebase esté en la misma carpeta que este script
#    y que se llame 'serviceAccountKey.json'.
FIREBASE_CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serviceAccountKey.json")

# Configuración de logging para depuración
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Precisión para cálculos decimales
getcontext().prec = 18

# Cliente global de Firestore
db = None

# Definición de estados para los flujos de conversación
(
    # Onboarding
    ONBOARDING_META, ONBOARDING_INGRESO, ONBOARDING_PLAN, ONBOARDING_PLAN_CUSTOM_NEC, ONBOARDING_PLAN_CUSTOM_DES, ONBOARDING_DEUDA_PREGUNTA,

    # Flujo para registrar un gasto detallado
    EXPENSE_DETAILED_MONTO, EXPENSE_DETAILED_CATEGORIA, EXPENSE_DETAILED_DESC, EXPENSE_DETAILED_TIPO,
    
    # Flujo para ejecutar Gasto Rápido
    EXPENSE_QUICK_SELECT,

    # Flujo para gestionar Gastos Rápidos (CRUD)
    QUICK_EXPENSE_CRUD_ACTION, QUICK_EXPENSE_ADD_NOMBRE, QUICK_EXPENSE_ADD_MONTO, 
    QUICK_EXPENSE_ADD_CATEGORIA, QUICK_EXPENSE_ADD_TIPO, QUICK_EXPENSE_DELETE_SELECT,

    # Flujo para manejar sobregiros
    OVERSPEND_CHOICE, OVERSPEND_MOVE_AMOUNT,

    # Flujo para registrar una aportación (ahorro/inversión)
    INVESTMENT_MONTO, INVESTMENT_DESC,

    ### CAMBIO: Añadidos nuevos estados para la gestión completa de deudas
    DEBT_MENU, DEBT_ACTION, DEBT_ADD_NOMBRE, DEBT_ADD_SALDO, DEBT_ADD_TASA, DEBT_ADD_PAGO, DEBT_ADD_CONFIRM,
    DEBT_EDIT_SELECT, DEBT_EDIT_FIELD, DEBT_EDIT_VALUE, DEBT_DELETE_SELECT, DEBT_PLAN_EXTRA,
    
    # Flujo para gestionar ingresos (CRUD)
    INCOME_CRUD_ACTION, INCOME_ADD_NOMBRE, INCOME_ADD_MONTO, 
    INCOME_EDIT_SELECT, INCOME_EDIT_NOMBRE, INCOME_EDIT_MONTO, 
    INCOME_DELETE_SELECT,

    # Flujo para editar el presupuesto
    EDIT_BUDGET_NEC, EDIT_BUDGET_DES
) = range(42) # CAMBIO: El rango aumenta por los nuevos estados

# ============================================================================ #
# SECTION 1: LÓGICA DE NEGOCIO Y CLASES FINANCIERAS
# ============================================================================ #

class Ingreso:
    def __init__(self, id, nombre, monto):
        self.id = id
        self.nombre = nombre
        self.monto = Decimal(monto)

class PresupuestoCategoria:
    def __init__(self, nombre, monto_asignado):
        self.nombre = nombre
        self.monto_asignado = Decimal(monto_asignado)

class Transaccion:
    def __init__(self, id, monto, categoria, tipo_gasto, descripcion, fecha):
        self.id = id
        self.monto = Decimal(monto)
        self.categoria = categoria
        self.tipo_gasto = tipo_gasto
        self.descripcion = descripcion
        self.fecha = fecha

class Deuda:
    def __init__(self, id, nombre, saldo_actual, tasa_interes_anual, pago_minimo_mensual):
        self.id = id
        self.nombre = nombre
        self.saldo_actual = Decimal(saldo_actual)
        self.tasa_interes_anual = Decimal(tasa_interes_anual)
        self.pago_minimo_mensual = Decimal(pago_minimo_mensual)
        
class GastoRapido:
    def __init__(self, id, nombre, monto, categoria, tipo_gasto):
        self.id = id
        self.nombre = nombre
        self.monto = Decimal(monto)
        self.categoria = categoria
        self.tipo_gasto = tipo_gasto

class TipManager:
    ### CAMBIO: Lógica de elección de tip mejorada y a prueba de errores.
    def elegir_uno(self, nivel, condicion, excluidos):
        if not db: return None
        try:
            # Consulta base más amplia para asegurar que traemos candidatos
            base_query = db.collection('tips_financieros').where(
                filter=firestore.FieldFilter("nivel_ingreso", "array_contains_any", [nivel, "Todos"])
            )
            
            candidatos_potenciales = base_query.stream()
            candidatos = []
            
            for tip_doc in candidatos_potenciales:
                tip_data = tip_doc.to_dict()
                condiciones_tip = tip_data.get("condicion", [])
                
                # Verificamos si la condición del usuario está en las del tip y si no ha sido mostrado
                if condicion in condiciones_tip and tip_doc.id not in excluidos:
                    tip_data['id'] = tip_doc.id
                    candidatos.append(tip_data)

            if not candidatos: return None
            
            return random.choice(candidatos)
        except Exception as e:
            logger.error(f"Error al elegir tip desde Firestore: {e}")
            return None

class PlanificadorFinanciero:
    def __init__(self, telegram_id):
        self.telegram_id = telegram_id
        self.tip_manager = TipManager()
        self._inicializar_datos()

    def _inicializar_datos(self):
        self.ingresos = []
        self.transacciones = []
        self.deudas = []
        self.gastos_rapidos = []
        self.meta_principal = ""
        self.tips_mostrados_ids = []
        self.budget_percentages = {"Necesidades": Decimal('0.5'), "Deseos": Decimal('0.3'), "Inversión": Decimal('0.2')}
        self.sobregiros_mes_actual = {}

    def cargar_datos_desde_firestore(self, user_doc, sub_collections_data):
        if not user_doc.exists: return

        user_data = user_doc.to_dict()
        self.meta_principal = user_data.get('meta_principal', '')
        self.tips_mostrados_ids = user_data.get('tips_mostrados_ids', [])
        self.sobregiros_mes_actual = {k: Decimal(v) for k, v in user_data.get('sobregiros_mes_actual', {}).items()}
        
        budget_pct_raw = user_data.get('budget_percentages', {})
        if budget_pct_raw:
             if "Ahorro/Deudas" in budget_pct_raw:
                 budget_pct_raw["Inversión"] = budget_pct_raw.pop("Ahorro/Deudas")
                 user_doc.reference.update({'budget_percentages': {k: str(v) for k, v in budget_pct_raw.items()}})

             self.budget_percentages = {k: Decimal(v) for k, v in budget_pct_raw.items()}

        self.ingresos = [Ingreso(doc.id, **doc.to_dict()) for doc in sub_collections_data.get('ingresos', [])]
        self.transacciones = [Transaccion(doc.id, **doc.to_dict()) for doc in sub_collections_data.get('transacciones', [])]
        self.deudas = [Deuda(doc.id, **doc.to_dict()) for doc in sub_collections_data.get('deudas', [])]
        self.gastos_rapidos = [GastoRapido(doc.id, **doc.to_dict()) for doc in sub_collections_data.get('gastos_rapidos', [])]

    def _ingreso_mensual_total(self):
        return sum(i.monto for i in self.ingresos) if self.ingresos else Decimal('0.0')

    def get_presupuestos_calculados(self):
        total = self._ingreso_mensual_total()
        return [
            PresupuestoCategoria(nombre, total * pct)
            for nombre, pct in self.budget_percentages.items()
        ]

    def nivel_por_ingreso(self):
        total = self._ingreso_mensual_total()
        if total < 9000: return "Nivel 1"
        if total < 30000: return "Nivel 2"
        if total < 80000: return "Nivel 3"
        if total < 150000: return "Nivel 4"
        return "Nivel 5"

    def condicion_por_deuda(self):
        return "Con deudas" if self.deudas else "Sin deudas"

    def siguiente_tip(self):
        nivel = self.nivel_por_ingreso()
        condicion = self.condicion_por_deuda()
        excluidos = set(self.tips_mostrados_ids)
        tip = self.tip_manager.elegir_uno(nivel, condicion, excluidos)
        
        # Si no hay tips nuevos y ya hemos mostrado algunos, reiniciamos la lista
        if tip is None and self.tips_mostrados_ids:
            self.tips_mostrados_ids = []
            tip = self.tip_manager.elegir_uno(nivel, condicion, set())
        
        if tip and 'id' in tip:
            self.tips_mostrados_ids.append(tip["id"])
            # Guardamos el cambio en Firestore
            if db:
                user_ref = db.collection('usuarios').document(str(self.telegram_id))
                user_ref.update({'tips_mostrados_ids': self.tips_mostrados_ids})
            
        return tip

    def calcular_gastos_reales_por_tipo(self):
        gastos = {"Necesidades": Decimal('0'), "Deseos": Decimal('0'), "Inversión": Decimal('0')}
        now = datetime.now()
        transacciones_mes = [t for t in self.transacciones if t.fecha and t.fecha.month == now.month and t.fecha.year == now.year]
        for t in transacciones_mes:
            tipo = t.tipo_gasto
            if tipo == "Ahorro/Deudas": tipo = "Inversión"
            if tipo in gastos: gastos[tipo] += t.monto
        return gastos
    
    def _generar_plan_pago_deuda(self, deudas_ordenadas, dinero_extra_mensual):
        if not deudas_ordenadas: return "No tienes deudas registradas."
        plan = []
        extra = Decimal(dinero_extra_mensual)
        total_pagos_minimos = sum(d.pago_minimo_mensual for d in deudas_ordenadas)
        
        plan.append(f"1. Paga el mínimo en *TODAS* tus deudas (${total_pagos_minimos:,.2f} al mes).")
        plan.append(f"2. Usa tu dinero extra mensual (${extra:,.2f}) para atacar la *primera deuda*.")
        for i, deuda in enumerate(deudas_ordenadas):
            plan.append(
                f"\n*Prioridad #{i+1}: {deuda.nombre}*\n"
                f"  - Saldo: `${deuda.saldo_actual:,.2f}`\n"
                f"  - Tasa: `{deuda.tasa_interes_anual / 100:.2%}`"
            )
        plan.append("\n3. Al liquidar una deuda, ¡suma su pago mínimo al dinero extra y ataca la siguiente!")
        return "\n".join(plan)

    def generar_plan_avalancha(self, dinero_extra_mensual):
        deudas_ordenadas = sorted(self.deudas, key=lambda x: x.tasa_interes_anual, reverse=True)
        return self._generar_plan_pago_deuda(deudas_ordenadas, dinero_extra_mensual)

    def generar_plan_bola_de_nieve(self, dinero_extra_mensual):
        deudas_ordenadas = sorted(self.deudas, key=lambda x: x.saldo_actual)
        return self._generar_plan_pago_deuda(deudas_ordenadas, dinero_extra_mensual)

# ============================================================================ #
# SECTION 2: GESTIÓN DE BASE DE DATOS (FIRESTORE)
# ============================================================================ #

def initialize_firebase():
    global db
    if not os.path.exists(FIREBASE_CREDENTIALS_PATH):
        logger.error(f"FATAL: No se encontró '{FIREBASE_CREDENTIALS_PATH}'.")
        return
    try:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Conexión con Firebase establecida.")
    except Exception as e:
        logger.error(f"Error al inicializar Firebase: {e}")
        db = None

def initialize_database_content():
    if not db: return
    tips_collection = db.collection('tips_financieros')
    if next(tips_collection.limit(1).stream(), None): return

    logger.info("Poblando 'tips_financieros' desde JSON...")
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tips_financieros.json')
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            tips = json.load(f)
        batch = db.batch()
        for tip in tips:
            batch.set(tips_collection.document(tip['id']), tip)
        batch.commit()
        logger.info(f"Se insertaron {len(tips)} tips.")
    except Exception as e:
        logger.error(f"Error al poblar la colección de tips: {e}")

async def get_user_planner(telegram_id: int, context: ContextTypes.DEFAULT_TYPE | None = None) -> PlanificadorFinanciero:
    """
    Obtiene el planificador financiero para un usuario de Telegram.  Si se
    proporciona un objeto `context`, utilizará un caché en memoria basado en
    `context.user_data` para evitar lecturas repetidas desde Firestore.  En
    caso de error al consultar la base de datos, devuelve un planificador
    vacío y almacena dicho objeto en caché para evitar reintentos en el mismo
    ciclo de ejecución.
    """
    # Comprobar caché si se proporciona context
    if context is not None:
        cached = context.user_data.get('planner')
        if cached is not None:
            logger.debug(f"[planner] cache HIT for user {telegram_id}")
            return cached
    
    logger.debug(f"[planner] cache MISS for user {telegram_id}")
    planner = PlanificadorFinanciero(telegram_id)
    if not db:
        # Si no hay conexión a la base de datos, almacenamos el planificador vacío en caché
        if context is not None:
            context.user_data['planner'] = planner
        return planner

    try:
        user_ref = db.collection('usuarios').document(str(telegram_id))
        user_doc = user_ref.get()

        now = datetime.now()
        start_of_month = datetime(now.year, now.month, 1)

        transactions_query = user_ref.collection('transacciones').where(
            filter=firestore.FieldFilter("fecha", ">=", start_of_month)
        ).stream()
        
        sub_collections_data = {
            'ingresos': list(user_ref.collection('ingresos').stream()),
            'transacciones': list(transactions_query),
            'deudas': list(user_ref.collection('deudas').stream()),
            'gastos_rapidos': list(user_ref.collection('gastos_rapidos').stream()),
        }
        
        planner.cargar_datos_desde_firestore(user_doc, sub_collections_data)
        # Guardamos en caché para este usuario
        if context is not None:
            context.user_data['planner'] = planner
        return planner
    except Exception as e:
        logger.error(f"Error al cargar datos del usuario {telegram_id}: {e}")
        # Almacenar el planificador vacío para evitar reintentos inmediatos
        if context is not None:
            context.user_data['planner'] = planner
        return planner

# Utilidad para invalidar el caché del planificador para un usuario
def invalidate_planner_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Elimina la entrada 'planner' del diccionario de datos de usuario (`user_data`)
    asociado al contexto.  Debe invocarse después de cualquier operación que
    modifique los datos del usuario en Firestore (crear, actualizar o
    eliminar ingresos, gastos, deudas, presupuestos, etc.).
    """
    if context and 'planner' in context.user_data:
        context.user_data.pop('planner', None)

async def save_document(collection_path: list, data: dict, document_id: str = None):
    if not db: return None
    try:
        sanitized_data = {k: str(v) if isinstance(v, Decimal) else v for k, v in data.items()}
        
        is_transaction = any('transacciones' in s for s in collection_path)
        if is_transaction:
             sanitized_data['fecha'] = firestore.SERVER_TIMESTAMP

        ref = db.collection(collection_path[0])
        for i in range(1, len(collection_path), 2):
            ref = ref.document(collection_path[i]).collection(collection_path[i+1])

        if document_id:
            doc_ref = ref.document(document_id)
            doc_ref.set(sanitized_data, merge=True)
            return doc_ref.id
        else:
            _, doc_ref = ref.add(sanitized_data)
            return doc_ref.id
    except Exception as e:
        logger.error(f"Error al guardar documento en {'/'.join(collection_path)}: {e}")
        return None

async def delete_document(collection_path: list):
    if not db: return False
    try:
        # La ruta a un documento debe tener un número par de elementos
        if len(collection_path) % 2 != 0:
            logger.error(f"Ruta inválida para eliminación: {collection_path}")
            return False

        # Construimos la referencia iterativamente
        doc_ref = db.collection(collection_path[0])
        for i in range(1, len(collection_path)):
            if i % 2 == 1: # Es un ID de documento
                doc_ref = doc_ref.document(collection_path[i])
            else: # Es un nombre de colección
                doc_ref = doc_ref.collection(collection_path[i])

        doc_ref.delete()
        logger.info(f"Documento en {'/'.join(collection_path)} eliminado con éxito.")
        return True
    except Exception as e:
        logger.error(f"Error al eliminar documento en {'/'.join(collection_path)}: {e}")
        return False
    
async def get_user_summary_and_budget(telegram_id: int) -> tuple[dict, list, dict]:
    """
    Función ultraligera que obtiene solo el documento principal del usuario.
    Retorna el resumen de gastos del mes, los porcentajes de presupuesto y el ingreso total.
    """
    if not db: return {}, [], {}

    try:
        user_ref = db.collection('usuarios').document(str(telegram_id))
        user_doc = user_ref.get()

        if not user_doc.exists:
            return {}, [], {}

        user_data = user_doc.to_dict()
            
        # Obtener el resumen del mes actual
        clave_mes = datetime.now().strftime('%Y-%m')
        resumen_mensual = user_data.get('resumen_mensual', {}).get(clave_mes, {})
        # Convertir valores a Decimal para consistencia
        gastos_resumen = {k: Decimal(str(v)) for k, v in resumen_mensual.items()}

        # Obtener porcentajes de presupuesto
        budget_pct_raw = user_data.get('budget_percentages', {})
        budget_percentages = {k: Decimal(v) for k, v in budget_pct_raw.items()}
            
        return gastos_resumen, budget_percentages, user_data

    except Exception as e:
        logger.error(f"Error al cargar el resumen del usuario {telegram_id}: {e}")
        return {}, [], {}


# ============================================================================ #
# SECTION 3: FUNCIONES AUXILIARES Y DE INTERFAZ
# ============================================================================ #

def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in str(text))

async def parse_decimal_input(text: str) -> Decimal | None:
    try:
        cleaned_text = text.replace('$', '').replace(',', '').strip()
        return Decimal(cleaned_text)
    except (InvalidOperation, TypeError):
        return None

async def check_user_exists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not db:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Error de conexión con la base de datos.")
        return False
        
    user_ref = db.collection('usuarios').document(str(update.effective_user.id))
    doc = user_ref.get()
    if not doc.exists:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Parece que eres un nuevo usuario. Por favor, inicia con /start para configurar tu perfil."
        )
        return False
    return True

# ============================================================================ #
# SECTION 4: COMANDOS Y MENÚ PRINCIPAL
# ============================================================================ #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not db:
        await update.message.reply_text("❌ Error de conexión. Inténtalo más tarde.")
        return ConversationHandler.END

    user_ref = db.collection('usuarios').document(str(user.id))
    doc = user_ref.get()
    if doc.exists:
        await update.message.reply_text(f"¡Hola de nuevo, {user.first_name}! 👋\nUsa /menu para ver tus opciones.")
        return ConversationHandler.END
    
    context.user_data['onboarding_data'] = {}
    keyboard = [
        [InlineKeyboardButton("Pagar mis deudas", callback_data="meta_Pagar mis deudas")],
        [InlineKeyboardButton("Ahorrar para una meta", callback_data="meta_Ahorrar para una meta")],
        [InlineKeyboardButton("Empezar a invertir", callback_data="meta_Empezar a invertir")],
        [InlineKeyboardButton("Solo entender mis gastos", callback_data="meta_Solo entender mis gastos")],
    ]
    await update.message.reply_text(
        f"¡Hola, {user.first_name}! 👋 Soy tu asistente financiero.\n\n"
        "Para empezar, ¿cuál es tu meta principal?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ONBOARDING_META

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await main_menu_with_planner(update, context, planner=None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query: await query.answer()
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Operación cancelada.")
    context.user_data.clear()
    await main_menu(update, context)
    return ConversationHandler.END
    
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await main_menu(update, context)
    return ConversationHandler.END

async def back_to_edit_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await edit_profile_menu(update, context)
    return ConversationHandler.END

async def back_to_expense_hub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await expense_hub(update, context)
    return ConversationHandler.END
    
# ============================================================================ #
# SECTION 5: FLUJO DE ONBOARDING
# ============================================================================ #

async def onboarding_meta_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['onboarding_data']['meta_principal'] = query.data.split('_', 1)[1]
    await query.edit_message_text(text="¡Excelente meta! Ahora, escribe tu *ingreso mensual total* después de impuestos. Ejemplo: 15000", parse_mode=ParseMode.MARKDOWN)
    return ONBOARDING_INGRESO

async def onboarding_ingreso_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ingreso = await parse_decimal_input(update.message.text)
    if ingreso is None or ingreso <= 0:
        await update.message.reply_text("Por favor, ingresa un número válido y positivo.")
        return ONBOARDING_INGRESO

    context.user_data['onboarding_data']['ingreso_monto'] = ingreso
    necesidades, deseos, ahorro = ingreso * Decimal('0.5'), ingreso * Decimal('0.3'), ingreso * Decimal('0.2')

    texto = (
        f"¡Perfecto! Con ${ingreso:,.2f}, te recomiendo este plan (50/30/20):\n\n"
        f"• *Necesidades (50%):* ${necesidades:,.2f}\n"
        f"• *Deseos (30%):* ${deseos:,.2f}\n"
        f"• *Inversión (20%):* ${ahorro:,.2f}\n\n"
        "¿Te parece bien para comenzar?"
    )
    keyboard = [
        [InlineKeyboardButton("Sí, usar este plan", callback_data="plan_si")],
        [InlineKeyboardButton("No, quiero personalizarlo", callback_data="plan_no")]
    ]
    await update.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return ONBOARDING_PLAN

async def onboarding_plan_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "plan_si":
        context.user_data['onboarding_data']['budget_percentages'] = {"Necesidades": '0.5', "Deseos": '0.3', "Inversión": '0.2'}
        await query.edit_message_text("¡Genial! Tu plan 50/30/20 está configurado.")
        keyboard = [[InlineKeyboardButton("Sí", callback_data="deuda_si")], [InlineKeyboardButton("No", callback_data="deuda_no")]]
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Última pregunta: ¿tienes deudas activas?", reply_markup=InlineKeyboardMarkup(keyboard))
        return ONBOARDING_DEUDA_PREGUNTA
    else:
        await query.edit_message_text("Entendido. La suma de los tres debe ser 100%.\n\nEscribe el nuevo porcentaje para *Necesidades* (ej. 50).", parse_mode=ParseMode.MARKDOWN)
        return ONBOARDING_PLAN_CUSTOM_NEC
        
async def onboarding_custom_nec_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    p_nec = await parse_decimal_input(update.message.text)
    if p_nec is None or not (0 <= p_nec <= 100):
        await update.message.reply_text("Porcentaje inválido. Ingresa un número entre 0 y 100.")
        return ONBOARDING_PLAN_CUSTOM_NEC
    context.user_data['p_nec'] = p_nec
    restante = 100 - p_nec
    await update.message.reply_text(f"Te queda {restante}% para distribuir. ¿Qué porcentaje quieres para *Deseos*?", parse_mode=ParseMode.MARKDOWN)
    return ONBOARDING_PLAN_CUSTOM_DES

async def onboarding_custom_des_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    p_des = await parse_decimal_input(update.message.text)
    p_nec = context.user_data['p_nec']
    restante = 100 - p_nec
    if p_des is None or not (0 <= p_des <= restante):
        await update.message.reply_text(f"Porcentaje inválido. Ingresa un número entre 0 y {restante}.")
        return ONBOARDING_PLAN_CUSTOM_DES
    
    p_aho = 100 - p_nec - p_des
    context.user_data['onboarding_data']['budget_percentages'] = {
        "Necesidades": str(p_nec / 100), "Deseos": str(p_des / 100), "Inversión": str(p_aho / 100)
    }
    await update.message.reply_text(f"¡Perfecto! Tu presupuesto personalizado es:\nNecesidades: {p_nec}%, Deseos: {p_des}%, Inversión: {p_aho}%")
    keyboard = [[InlineKeyboardButton("Sí", callback_data="deuda_si")], [InlineKeyboardButton("No", callback_data="deuda_no")]]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Última pregunta: ¿tienes deudas activas?", reply_markup=InlineKeyboardMarkup(keyboard))
    return ONBOARDING_DEUDA_PREGUNTA
    
async def onboarding_deuda_pregunta_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "deuda_si":
        await query.edit_message_text("Entendido. Podrás agregarlas desde el menú principal.")
    else:
        await query.edit_message_text("¡Perfecto! No registraremos deudas por ahora.")
    await finalizar_onboarding(update, context)
    return ConversationHandler.END

async def finalizar_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get('onboarding_data', {})
    user_id = str(update.effective_user.id)
    user_data_main = {
        'nombre_usuario': update.effective_user.full_name,
        'meta_principal': data.get('meta_principal'),
        'budget_percentages': data.get('budget_percentages'),
        'tips_mostrados_ids': [],
        'sobregiros_mes_actual': {}
    }
    await save_document(['usuarios'], user_data_main, document_id=user_id)
    ingreso_data = {'nombre': "Ingreso Principal", 'monto': data.get('ingreso_monto')}
    await save_document(['usuarios', user_id, 'ingresos'], ingreso_data)
    await context.bot.send_message(
        chat_id=user_id,
        text="🚀 ¡Tu perfil ha sido creado! Usa /menu para empezar."
    )
    context.user_data.clear()

# ============================================================================ #
# SECTION 6: FLUJOS DE GASTOS Y SOBREGIROS
# ============================================================================ #

async def expense_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📝 Registro Detallado", callback_data="expense_detailed_start")],
        [InlineKeyboardButton("⚡ Gasto Rápido (Atajos)", callback_data="expense_quick_start")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="main_menu")]
    ]
    await query.edit_message_text("💸 ¿Cómo quieres registrar tu gasto?", reply_markup=InlineKeyboardMarkup(keyboard))

async def expense_detailed_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    # Cargamos el planificador usando caché de context
    planner = await get_user_planner(update.effective_user.id, context)
    context.user_data['planner'] = planner
    
    await query.edit_message_text("Ok, registro detallado. Por favor, escribe el monto del gasto.")
    context.user_data['expense_data'] = {}
    return EXPENSE_DETAILED_MONTO

async def expense_detailed_monto_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    monto = await parse_decimal_input(update.message.text)
    if monto is None or monto <= 0:
        await update.message.reply_text("Monto inválido. Por favor, ingresa un número positivo.")
        return EXPENSE_DETAILED_MONTO
    
    context.user_data['expense_data']['monto'] = monto
    keyboard = [
        [InlineKeyboardButton("Comida", callback_data="cat_Comida"), InlineKeyboardButton("Transporte", callback_data="cat_Transporte")],
        [InlineKeyboardButton("Hogar", callback_data="cat_Hogar"), InlineKeyboardButton("Entretenimiento", callback_data="cat_Entretenimiento")],
        [InlineKeyboardButton("Salud", callback_data="cat_Salud"), InlineKeyboardButton("Otro", callback_data="cat_Otro")],
    ]
    await update.message.reply_text("¿A qué categoría pertenece?", reply_markup=InlineKeyboardMarkup(keyboard))
    return EXPENSE_DETAILED_CATEGORIA

async def expense_detailed_categoria_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['expense_data']['categoria'] = query.data.split('_')[1]
    await query.edit_message_text("Escribe una breve descripción (ej. 'Café con amigos').")
    return EXPENSE_DETAILED_DESC

async def expense_detailed_desc_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['expense_data']['descripcion'] = update.message.text
    keyboard = [
        [InlineKeyboardButton("Necesidad", callback_data="tipo_Necesidades")],
        [InlineKeyboardButton("Deseo", callback_data="tipo_Deseos")]
    ]
    await update.message.reply_text("¿Fue una necesidad o un deseo?", reply_markup=InlineKeyboardMarkup(keyboard))
    return EXPENSE_DETAILED_TIPO

async def expense_detailed_tipo_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['expense_data']['tipo_gasto'] = query.data.split('_')[1]
    
    planner = context.user_data.get('planner')
    return await save_transaction_and_check_overspending(update, context, planner, context.user_data['expense_data'])

async def save_transaction_and_check_overspending(update: Update, context: ContextTypes.DEFAULT_TYPE, planner: PlanificadorFinanciero, transaccion_data: dict) -> int:
    query = update.callback_query
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    async def reply(text, reply_markup=None, parse_mode=None):
        try:
            if query and query.message:
                await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
            else:
                if update.message:
                    await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                logger.warning(f"Error leve al responder: {e}. Enviando mensaje nuevo como fallback.")
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

    transaction_id = await save_document(['usuarios', user_id, 'transacciones'], transaccion_data)
    if not transaction_id:
        await reply("❌ Error al guardar la transacción. Inténtalo de nuevo.")
        return ConversationHandler.END
    # --- INICIA CÓDIGO A AÑADIR ---
# Una vez guardada la transacción, actualizamos el resumen mensual
    if transaction_id:
        # Invalida el caché del planificador al realizar una transacción exitosa
        invalidate_planner_cache(context)
        try:
            monto_gasto = Decimal(transaccion_data['monto'])
            tipo_gasto = transaccion_data['tipo_gasto']
            
            # Obtenemos la clave del mes actual, ej: "2025-09"
            clave_mes = datetime.now().strftime('%Y-%m')
            
            # Usamos "dot notation" para actualizar un campo anidado en el mapa
            campo_a_incrementar = f'resumen_mensual.{clave_mes}.{tipo_gasto}'
            
            user_ref = db.collection('usuarios').document(user_id)
            user_ref.update({
                # firestore.Increment necesita un float o int, no un Decimal
                campo_a_incrementar: firestore.Increment(float(monto_gasto))
            })
            logger.info(f"Resumen mensual actualizado para usuario {user_id}: {campo_a_incrementar} +{monto_gasto}")
        except Exception as e:
            # Si esto falla, no debe detener el flujo principal, solo registrar el error
            logger.error(f"Error al actualizar el resumen mensual para {user_id}: {e}")
    # --- TERMINA CÓDIGO A AÑADIR ---


    # Recargamos el planificador con el contexto para tener los datos más frescos
    planner = await get_user_planner(user_id, context)

    gastos_reales = planner.calcular_gastos_reales_por_tipo()
    presupuestos = {p.nombre: p.monto_asignado for p in planner.get_presupuestos_calculados()}
    tipo_gasto = transaccion_data['tipo_gasto']
    presupuesto_cat = presupuestos.get(tipo_gasto, Decimal('0'))
    gastado_cat = gastos_reales.get(tipo_gasto, Decimal('0'))
    restante = presupuesto_cat - gastado_cat

    if restante < 0:
        sobregiro = abs(restante)
        context.user_data['overspend_info'] = {
            'categoria_excedida': tipo_gasto, 'sobregiro': sobregiro, 'causa': transaccion_data.get('descripcion', 'Gasto')
        }
        texto = f"📉 ¡Atención! Con tu registro en \"{transaccion_data['descripcion']}\", has excedido tu presupuesto de *{tipo_gasto}* por *${sobregiro:,.2f}*."
        
        opciones_disponibles = []
        for p_nombre, p_monto in presupuestos.items():
            if p_nombre != tipo_gasto:
                p_restante = p_monto - gastos_reales.get(p_nombre, Decimal('0'))
                if p_restante > 0:
                    opciones_disponibles.append((p_nombre, p_restante))

        keyboard = []
        if opciones_disponibles:
            texto += "\n\n¿Quieres mover fondos de otra categoría para cubrirlo?"
            for nombre, disponible in opciones_disponibles:
                keyboard.append([InlineKeyboardButton(f"Mover de {nombre} (${disponible:,.2f})", callback_data=f"overspend_move_{nombre}")])
        keyboard.append([InlineKeyboardButton("Dejarlo así por ahora", callback_data="overspend_leave")])
        await reply(texto, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        return OVERSPEND_CHOICE
    else:
        mensaje_exito = f"✅ ¡Gasto registrado con éxito!\nTe quedan *${restante:,.2f}* en '{tipo_gasto}'."
        if transaccion_data.get('categoria') == 'Aportación':
            mensaje_exito = f"🌱 ¡Aportación registrada! Sigue así.\nHas destinado *${gastado_cat:,.2f}* de *${presupuesto_cat:,.2f}* a '{tipo_gasto}' este mes."

        await reply(mensaje_exito, parse_mode=ParseMode.MARKDOWN)
        context.user_data.clear()
        await main_menu_with_planner(update, context, planner)
        return ConversationHandler.END

async def main_menu_with_planner(update: Update, context: ContextTypes.DEFAULT_TYPE, planner: PlanificadorFinanciero = None):
        query = update.callback_query
        if query: 
            await query.answer()

        if not await check_user_exists(update, context):
            return

        user_id = update.effective_user.id
        
        # --- OPTIMIZACIÓN CLAVE ---
        # Usamos la nueva función ultrarrápida en lugar de get_user_planner
        gastos, budget_percentages, user_data = await get_user_summary_and_budget(user_id)
        
        # Si no hay datos, mostramos un mensaje de error o guía
        if not budget_percentages:
            await context.bot.send_message(chat_id=user_id, text="No se pudo cargar tu perfil. Intenta con /start.")
            return

        # Para calcular el presupuesto, necesitamos el ingreso total. Lo leemos de Firestore también.
        ingresos_docs = db.collection('usuarios').document(str(user_id)).collection('ingresos').stream()
        ingreso_total = sum(Decimal(str(i.to_dict().get('monto', 0))) for i in ingresos_docs)

        reporte = ["*📊 Estado del Mes*"]
        categorias_ordenadas = ["Necesidades", "Deseos", "Inversión"]

        for categoria_nombre in categorias_ordenadas:
            pct = budget_percentages.get(categoria_nombre, Decimal('0'))
            presupuesto_asignado = ingreso_total * pct
            
            gastado_val = gastos.get(categoria_nombre, Decimal('0'))
            restante_val = presupuesto_asignado - gastado_val
            emoji = "✅" if restante_val >= 0 else "❌"
            
            nombre_cat = escape_markdown_v2(categoria_nombre)
            gastado = escape_markdown_v2(f"{gastado_val:,.2f}")
            restante = escape_markdown_v2(f"{restante_val:,.2f}")
            asignado = escape_markdown_v2(f"{presupuesto_asignado:,.2f}")

            linea = (
                f"*{emoji} {nombre_cat}:*\n"
                f"  Gastado: `${gastado}`\n"
                f"  Restante: `${restante}` de `${asignado}`"
            )
            reporte.append(linea)

        # La lógica de sobregiros pendientes se mantiene igual
        sobregiros = user_data.get('sobregiros_mes_actual', {})
        if sobregiros:
            reporte.append("\n*⚠️ Sobregiros Pendientes*")
            for cat, monto in sobregiros.items():
                if Decimal(monto) > 0:
                    cat_esc = escape_markdown_v2(cat)
                    monto_esc = escape_markdown_v2(f"{Decimal(monto):,.2f}")
                    reporte.append(f"  \\- {cat_esc}: `${monto_esc}`")
            
        texto_menu = "\n\n".join(reporte)

        keyboard = [
            [InlineKeyboardButton("💸 Registrar Gasto", callback_data="expense_hub")],
            [InlineKeyboardButton("🌱 Registrar Aportación", callback_data="investment_start")],
            [InlineKeyboardButton("💳 Gestionar Deudas", callback_data="debt_menu")],
            [InlineKeyboardButton("📄 Ver Reporte Completo", callback_data="full_report")],
            [InlineKeyboardButton("💡 Dame un Tip", callback_data="get_tip")],
            [InlineKeyboardButton("⚙️ Editar Perfil", callback_data="edit_profile_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        chat_id = update.effective_chat.id
        try:
            if query and query.message:
                await query.edit_message_text(text=texto_menu, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await context.bot.send_message(chat_id=chat_id, text=texto_menu, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                 logger.error(f"Error en main_menu_with_planner: {e}")
                 await context.bot.send_message(chat_id=chat_id, text=texto_menu, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

async def overspend_choice_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    overspend_info = context.user_data['overspend_info']
    
    if query.data == "overspend_leave":
        user_ref = db.collection('usuarios').document(user_id)
        user_ref.set({'sobregiros_mes_actual': {overspend_info['categoria_excedida']: str(overspend_info['sobregiro'])}}, merge=True)
        # Invalida el caché del planificador ya que el estado del usuario ha cambiado
        invalidate_planner_cache(context)
        await query.edit_message_text("Ok, se ha registrado el sobregiro. Lo verás en tu menú principal.")
        await main_menu(update, context)
        return ConversationHandler.END

    elif query.data.startswith("overspend_move_"):
        cat_origen = query.data.split('_')[-1]
        context.user_data['overspend_info']['cat_origen'] = cat_origen
        
        # Recargamos el planificador con caché para obtener datos actuales
        planner = await get_user_planner(user_id, context)
        gastos_reales = planner.calcular_gastos_reales_por_tipo()
        presupuestos = {p.nombre: p.monto_asignado for p in planner.get_presupuestos_calculados()}
        disponible = presupuestos[cat_origen] - gastos_reales.get(cat_origen, Decimal('0'))
        
        monto_sugerido = min(overspend_info['sobregiro'], disponible)
        
        await query.edit_message_text(f"Tienes ${disponible:,.2f} en '{cat_origen}'. ¿Cuánto quieres mover? (Sugerido: ${monto_sugerido:,.2f})")
        return OVERSPEND_MOVE_AMOUNT

async def overspend_move_amount_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    monto_a_mover = await parse_decimal_input(update.message.text)
    user_id = str(update.effective_user.id)
    overspend_info = context.user_data['overspend_info']
    cat_origen = overspend_info['cat_origen']
    cat_destino = overspend_info['categoria_excedida']

    # Recargamos el planificador con caché para obtener el estado actual
    planner = await get_user_planner(user_id, context)
    gastos_reales = planner.calcular_gastos_reales_por_tipo()
    presupuestos = {p.nombre: p.monto_asignado for p in planner.get_presupuestos_calculados()}
    disponible = presupuestos[cat_origen] - gastos_reales.get(cat_origen, Decimal('0'))

    if monto_a_mover is None or monto_a_mover <= 0 or monto_a_mover > disponible:
        await update.message.reply_text(f"Monto inválido. Debe ser un número positivo y no mayor a ${disponible:,.2f}.")
        return OVERSPEND_MOVE_AMOUNT

    ingreso_total = planner._ingreso_mensual_total()
    if ingreso_total > 0:
        pct_origen = planner.budget_percentages.get(cat_origen, Decimal(0))
        pct_destino = planner.budget_percentages.get(cat_destino, Decimal(0))
        
        planner.budget_percentages[cat_origen] = pct_origen - (monto_a_mover / ingreso_total)
        planner.budget_percentages[cat_destino] = pct_destino + (monto_a_mover / ingreso_total)
    
    nuevos_porcentajes_str = {k: str(v) for k, v in planner.budget_percentages.items()}
    user_ref = db.collection('usuarios').document(user_id)
    user_ref.update({'budget_percentages': nuevos_porcentajes_str})
    
    sobregiro_restante = overspend_info['sobregiro'] - monto_a_mover
    if sobregiro_restante > 0.01:
        user_ref.set({'sobregiros_mes_actual': {cat_destino: str(sobregiro_restante)}}, merge=True)
        await update.message.reply_text(f"✅ Se movieron ${monto_a_mover:,.2f}. Aún queda un sobregiro de ${sobregiro_restante:,.2f}.")
    else:
        user_ref.update({f'sobregiros_mes_actual.{cat_destino}': firestore.DELETE_FIELD})
        await update.message.reply_text(f"✅ ¡Excelente! El sobregiro en '{cat_destino}' ha sido cubierto.")

    # Invalidamos el caché del planificador dado que se han movido fondos y/o actualizado sobregiros
    invalidate_planner_cache(context)
    await main_menu(update, context)
    return ConversationHandler.END

# ============================================================================ #
# SECTION 7: OTROS FLUJOS PRINCIPALES
# ============================================================================ #

async def investment_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    planner = await get_user_planner(update.effective_user.id, context)
    context.user_data['planner'] = planner
    await query.edit_message_text("🌱 Registrar Aportación.\n\nEscribe el monto que aportaste.")
    context.user_data['investment_data'] = {'categoria': 'Aportación', 'tipo_gasto': 'Inversión'}
    return INVESTMENT_MONTO

async def investment_monto_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    monto = await parse_decimal_input(update.message.text)
    if monto is None or monto <= 0:
        await update.message.reply_text("Monto inválido. Ingresa un número positivo.")
        return INVESTMENT_MONTO
    context.user_data['investment_data']['monto'] = monto
    await update.message.reply_text("Escribe una descripción (ej. 'Ahorro para viaje', 'Pago a Tarjeta').")
    return INVESTMENT_DESC

async def investment_desc_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['investment_data']['descripcion'] = update.message.text
    planner = context.user_data.get('planner')
    return await save_transaction_and_check_overspending(update, context, planner, context.user_data['investment_data'])

### CAMBIO: Función `get_tip` corregida
async def get_tip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    planner = await get_user_planner(update.effective_user.id, context)
    tip = planner.siguiente_tip()

    if tip:
        titulo = escape_markdown_v2(tip.get('titulo', 'Tip Financiero'))
        explicacion = escape_markdown_v2(tip.get('explicacion', 'Aquí va un gran consejo.'))
        texto = f"💡 *{titulo}*\n\n_{explicacion}_"
    else:
        texto = escape_markdown_v2("Parece que he agotado mis consejos por ahora. ¡Vuelve más tarde!")

    keyboard = [[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="main_menu")]]
    await query.edit_message_text(text=texto, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    
async def full_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    planner = await get_user_planner(update.effective_user.id, context)
    now = datetime.now()
    
    total_ingresos = planner._ingreso_mensual_total()
    gastos_reales = planner.calcular_gastos_reales_por_tipo()
    presupuestos = planner.get_presupuestos_calculados()
    
    mes_formateado = escape_markdown_v2(now.strftime('%B %Y'))
    ingresos_formateado = escape_markdown_v2(f"{total_ingresos:,.2f}")
    
    # CORRECCIÓN: Define un separador con los caracteres ya escapados
    separador = escape_markdown_v2("-------------------------")

    texto = [f"*📊 REPORTE COMPLETO \\- {mes_formateado}*"]
    texto.append(f"*Ingreso Total:* `${ingresos_formateado}`")
    texto.append(f"*{separador}*") # Usa el separador corregido
    
    for p in presupuestos:
        nombre_cat = escape_markdown_v2(p.nombre)
        monto_asignado = escape_markdown_v2(f"{p.monto_asignado:,.2f}")
        gastado_val = gastos_reales.get(p.nombre, Decimal('0'))
        gastado = escape_markdown_v2(f"{gastado_val:,.2f}")
        restante = escape_markdown_v2(f"{p.monto_asignado - gastado_val:,.2f}")

        texto.append(f"*{nombre_cat}*")
        texto.append(f"  • *Presupuesto:* `${monto_asignado}`")
        texto.append(f"  • *Registrado:* `${gastado}`")
        texto.append(f"  • *Restante:* `${restante}`")

    total_gastado_val = gastos_reales.get("Necesidades", Decimal('0')) + gastos_reales.get("Deseos", Decimal('0'))
    neto_val = total_ingresos - total_gastado_val - gastos_reales.get("Inversión", Decimal('0'))

    total_gastado = escape_markdown_v2(f"{total_gastado_val:,.2f}")
    neto = escape_markdown_v2(f"{neto_val:,.2f}")

    texto.append(f"*{separador}*") # Usa el separador corregido
    texto.append(f"*Total Gastado \\(Nec \\+ Deseos\\):* `${total_gastado}`")
    texto.append(f"*Balance Neto:* `${neto}`")

    mensaje_final = "\n".join(texto)

    keyboard = [[InlineKeyboardButton("⬅️ Volver al Menú", callback_data="main_menu")]]
    await query.edit_message_text(text=mensaje_final, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

# ============================================================================ #
# SECTION 8: FLUJOS DE EDICIÓN DE PERFIL
# ============================================================================ #

async def edit_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("💸 Gestionar Ingresos", callback_data="income_menu")],
        [InlineKeyboardButton("⚖️ Editar Porcentajes Presupuesto", callback_data="edit_budget_start")],
        [InlineKeyboardButton("⚡ Gestionar Gastos Rápidos", callback_data="quick_expense_menu")],
        [InlineKeyboardButton("⬅️ Volver al Menú Principal", callback_data="main_menu")]
    ]
    await query.edit_message_text("⚙️ *Editar Perfil Financiero*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- Ingresos (CRUD) ---
async def income_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    planner = await get_user_planner(update.effective_user.id, context)
    
    # Mantenemos el formato del título
    texto = ["*💸 Gestión de Ingresos*"]
    if not planner.ingresos:
        texto.append("No tienes ingresos registrados\\.")
    else:
        for i, ingreso in enumerate(planner.ingresos, 1):
            # Escapamos solo los datos variables para seguridad y formato correcto
            nombre_esc = escape_markdown_v2(ingreso.nombre)
            monto_esc = escape_markdown_v2(f"{ingreso.monto:,.2f}")
            # Escapamos el punto después del número de lista
            texto.append(f"{i}\\. {nombre_esc}: `${monto_esc}`")
    
    keyboard = [
        [InlineKeyboardButton("➕ Añadir", callback_data="income_add"), InlineKeyboardButton("🗑️ Eliminar", callback_data="income_delete")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="edit_profile_menu")]
    ]
    
    # Unimos el texto y lo enviamos sin escapado global
    await query.edit_message_text("\n".join(texto), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    return INCOME_CRUD_ACTION

async def income_crud_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data.split('_')[1]

    if action == "add":
        await query.edit_message_text("Nombre para el nuevo ingreso (ej. 'Freelance'):")
        return INCOME_ADD_NOMBRE
    elif action == "delete":
        planner = await get_user_planner(update.effective_user.id, context)
        if not planner.ingresos:
            await query.edit_message_text("No hay ingresos para eliminar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="income_menu")]]))
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton(f"{i.nombre} (${i.monto:,.2f})", callback_data=f"del_income_{i.id}")] for i in planner.ingresos]
        keyboard.append([InlineKeyboardButton("Cancelar", callback_data="cancel_op")])
        await query.edit_message_text("Selecciona el ingreso a eliminar:", reply_markup=InlineKeyboardMarkup(keyboard))
        return INCOME_DELETE_SELECT
    return ConversationHandler.END

async def income_add_nombre_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['income_data'] = {'nombre': update.message.text}
    await update.message.reply_text("Monto mensual para este ingreso:")
    return INCOME_ADD_MONTO

async def income_add_monto_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    monto = await parse_decimal_input(update.message.text)
    if monto is None or monto <= 0:
        await update.message.reply_text("Monto inválido.")
        return INCOME_ADD_MONTO
    
    context.user_data['income_data']['monto'] = monto
    user_id = str(update.effective_user.id)
    doc_id = await save_document(['usuarios', user_id, 'ingresos'], context.user_data['income_data'])
    if doc_id:
        await update.message.reply_text("✅ Listo. Tu ingreso se guardó correctamente.")
        invalidate_planner_cache(context)
    else:
        await update.message.reply_text("❌ Lo siento, ocurrió un error al guardar. Inténtalo en unos minutos.")
    await main_menu(update, context)
    return ConversationHandler.END
    
async def income_delete_select_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_op":
        await query.edit_message_text("Operación cancelada.")
    else:
        income_id_to_delete = query.data.split('_')[-1]
        user_id = str(update.effective_user.id)
        deleted = await delete_document(['usuarios', user_id, 'ingresos', income_id_to_delete])
        if deleted:
            invalidate_planner_cache(context)
            await query.edit_message_text("🗑️ Ingreso eliminado.")
        else:
            await query.edit_message_text("⚠️ No pude eliminarlo ahora mismo. Intenta nuevamente más tarde.")

    await main_menu(update, context)
    return ConversationHandler.END

# --- Flujos de Deudas (CRUD Completo) ---
### CAMBIO: Todo el flujo de gestión de deudas ha sido reescrito para ser un CRUD completo y robusto.

async def debt_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Muestra el menú principal de gestión de deudas y sirve como punto de re-entrada."""
    query = update.callback_query
    if query: await query.answer()
    
    planner = await get_user_planner(update.effective_user.id, context)
    
    texto = ["*💳 Gestión de Deudas*"]
    if not planner.deudas:
        texto.append("\nNo tienes deudas registradas.")
    else:
        texto.append("\nTus deudas actuales:")
        for d in planner.deudas:
            texto.append(f"• *{d.nombre}*: ${d.saldo_actual:,.2f} al {d.tasa_interes_anual}%")

    keyboard = [
        [InlineKeyboardButton("➕ Añadir Deuda", callback_data="debt_add")],
        [InlineKeyboardButton("✏️ Editar Deuda", callback_data="debt_edit")],
        [InlineKeyboardButton("🗑️ Eliminar Deuda", callback_data="debt_delete")],
        [InlineKeyboardButton("📊 Generar Plan de Pago", callback_data="debt_plan")],
        [InlineKeyboardButton("⬅️ Volver al Menú", callback_data="main_menu")],
    ]
    
    message_text = escape_markdown_v2("\n".join(texto))
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query and query.message:
        await query.edit_message_text(text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

    return DEBT_ACTION

async def debt_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Maneja la acción seleccionada en el menú de deudas."""
    query = update.callback_query
    await query.answer()
    action = query.data.split('_')[1]
    user_id = str(update.effective_user.id)
    
    context.user_data['debt_data'] = {}

    if action == "add":
        await query.edit_message_text("Okay, vamos a añadir una nueva deuda.\n\nPrimero, escribe el *nombre* (ej. 'Tarjeta de Crédito', 'Préstamo Personal').", parse_mode=ParseMode.MARKDOWN)
        return DEBT_ADD_NOMBRE
    
    planner = await get_user_planner(user_id, context)
    if not planner.deudas and action in ["edit", "delete", "plan"]:
        await query.edit_message_text("No tienes deudas registradas para realizar esta acción.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="debt_menu_back")]]))
        return DEBT_MENU

    if action == "edit":
        keyboard = [[InlineKeyboardButton(f"{d.nombre} (${d.saldo_actual:,.2f})", callback_data=f"edit_{d.id}")] for d in planner.deudas]
        keyboard.append([InlineKeyboardButton("Cancelar", callback_data="cancel_debt")])
        await query.edit_message_text("Selecciona la deuda que quieres editar:", reply_markup=InlineKeyboardMarkup(keyboard))
        return DEBT_EDIT_SELECT
        
    if action == "delete":
        keyboard = [[InlineKeyboardButton(f"{d.nombre} (${d.saldo_actual:,.2f})", callback_data=f"del_{d.id}")] for d in planner.deudas]
        keyboard.append([InlineKeyboardButton("Cancelar", callback_data="cancel_debt")])
        await query.edit_message_text("Selecciona la deuda a eliminar:", reply_markup=InlineKeyboardMarkup(keyboard))
        return DEBT_DELETE_SELECT
        
    if action == "plan":
        await query.edit_message_text("¿Cuánto dinero *extra* (adicional a los pagos mínimos) puedes destinar a tus deudas cada mes?", parse_mode=ParseMode.MARKDOWN)
        return DEBT_PLAN_EXTRA
        
    return DEBT_MENU

# --- Flujo para Añadir Deuda ---
async def debt_add_nombre_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['debt_data']['nombre'] = update.message.text
    await update.message.reply_text("Entendido. Ahora, ¿cuál es el *saldo actual* de la deuda?", parse_mode=ParseMode.MARKDOWN)
    return DEBT_ADD_SALDO

async def debt_add_saldo_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    saldo = await parse_decimal_input(update.message.text)
    if saldo is None or saldo < 0:
        await update.message.reply_text("Saldo inválido. Por favor, ingresa un número positivo.")
        return DEBT_ADD_SALDO
    context.user_data['debt_data']['saldo_actual'] = saldo
    await update.message.reply_text("Perfecto. ¿Cuál es la *tasa de interés anual* en porcentaje (%)?", parse_mode=ParseMode.MARKDOWN)
    return DEBT_ADD_TASA

async def debt_add_tasa_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tasa = await parse_decimal_input(update.message.text)
    if tasa is None or not (0 < tasa < 200):
        await update.message.reply_text("Tasa inválida. Ingresa un número realista (ej. 25 para 25%).")
        return DEBT_ADD_TASA
    context.user_data['debt_data']['tasa_interes_anual'] = tasa
    await update.message.reply_text("Casi listo. ¿Cuál es el *pago mínimo mensual*?", parse_mode=ParseMode.MARKDOWN)
    return DEBT_ADD_PAGO

async def debt_add_pago_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pago = await parse_decimal_input(update.message.text)
    if pago is None or pago < 0:
        await update.message.reply_text("Pago inválido. Ingresa un número positivo.")
        return DEBT_ADD_PAGO
    context.user_data['debt_data']['pago_minimo_mensual'] = pago
    
    data = context.user_data['debt_data']
    texto_confirm = (
        f"Por favor, confirma los datos:\n\n"
        f"  - *Nombre*: {data['nombre']}\n"
        f"  - *Saldo*: ${data['saldo_actual']:,.2f}\n"
        f"  - *Tasa Anual*: {data['tasa_interes_anual']}%\n"
        f"  - *Pago Mínimo*: ${data['pago_minimo_mensual']:,.2f}\n\n"
        "¿Es correcto?"
    )
    keyboard = [[InlineKeyboardButton("Sí, guardar", callback_data="confirm_yes")], [InlineKeyboardButton("No, cancelar", callback_data="confirm_no")]]
    await update.message.reply_text(texto_confirm, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return DEBT_ADD_CONFIRM

async def debt_add_confirm_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_yes":
        user_id = str(update.effective_user.id)
        doc_id = await save_document(['usuarios', user_id, 'deudas'], context.user_data['debt_data'])
        if doc_id:
            invalidate_planner_cache(context)
            await query.edit_message_text("✅ ¡Deuda añadida con éxito!")
        else:
            await query.edit_message_text("❌ Lo siento, ocurrió un error al guardar la deuda. Inténtalo más tarde.")
    else:
        await query.edit_message_text("Operación cancelada.")
    
    context.user_data.clear()
    # Volvemos al menú de deudas
    await debt_main_menu(update, context)
    return DEBT_MENU

# --- Flujo para Eliminar Deuda ---
async def debt_delete_select_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_debt":
        await query.edit_message_text("Operación cancelada.")
    else:
        debt_id = query.data.split('_')[-1]
        user_id = str(update.effective_user.id)
        deleted = await delete_document(['usuarios', user_id, 'deudas', debt_id])
        if deleted:
            invalidate_planner_cache(context)
            await query.edit_message_text("🗑️ Deuda eliminada con éxito.")
        else:
            await query.edit_message_text("❌ Error al eliminar la deuda.")
    
    await debt_main_menu(update, context)
    return DEBT_MENU

# --- Flujo para Editar Deuda ---
async def debt_edit_select_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_debt":
        await query.edit_message_text("Operación cancelada.")
        await debt_main_menu(update, context)
        return DEBT_MENU
    
    debt_id = query.data.split('_')[-1]
    context.user_data['debt_edit_id'] = debt_id
    
    keyboard = [
        [InlineKeyboardButton("Nombre", callback_data="editfield_nombre")],
        [InlineKeyboardButton("Saldo Actual", callback_data="editfield_saldo_actual")],
        [InlineKeyboardButton("Tasa de Interés", callback_data="editfield_tasa_interes_anual")],
        [InlineKeyboardButton("Pago Mínimo", callback_data="editfield_pago_minimo_mensual")],
        [InlineKeyboardButton("Cancelar", callback_data="cancel_debt")]
    ]
    await query.edit_message_text("¿Qué campo de la deuda quieres editar?", reply_markup=InlineKeyboardMarkup(keyboard))
    return DEBT_EDIT_FIELD

async def debt_edit_field_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_debt":
        await query.edit_message_text("Operación cancelada.")
        await debt_main_menu(update, context)
        return DEBT_MENU
        
    field = query.data.split('_')[-1]
    context.user_data['debt_edit_field'] = field
    
    await query.edit_message_text(f"Introduce el nuevo valor para *{field.replace('_', ' ').title()}*:", parse_mode=ParseMode.MARKDOWN)
    return DEBT_EDIT_VALUE

async def debt_edit_value_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field = context.user_data['debt_edit_field']
    new_value_raw = update.message.text
    
    new_value = new_value_raw
    if field != 'nombre':
        parsed_value = await parse_decimal_input(new_value_raw)
        if parsed_value is None or parsed_value < 0:
            await update.message.reply_text("Valor numérico inválido. Por favor, inténtalo de nuevo.")
            return DEBT_EDIT_VALUE
        new_value = parsed_value

    user_id = str(update.effective_user.id)
    debt_id = context.user_data['debt_edit_id']
    
    doc_id = await save_document(['usuarios', user_id, 'deudas'], {field: new_value}, document_id=debt_id)
    if doc_id:
        invalidate_planner_cache(context)
        await update.message.reply_text("✅ ¡Deuda actualizada!")
    else:
        await update.message.reply_text("❌ Lo siento, ocurrió un error al actualizar la deuda. Inténtalo de nuevo más tarde.")
    
    context.user_data.clear()
    await debt_main_menu(update, context)
    return DEBT_MENU
    
# --- Flujo para Plan de Pago ---
async def debt_plan_extra_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    extra = await parse_decimal_input(update.message.text)
    if extra is None or extra < 0:
        await update.message.reply_text("Monto inválido. Ingresa un número positivo.")
        return DEBT_PLAN_EXTRA
        
    planner = await get_user_planner(update.effective_user.id, context)
    avalancha = planner.generar_plan_avalancha(extra)
    bola_nieve = planner.generar_plan_bola_de_nieve(extra)
    
    texto = (
        f"*🚀 PLAN AVALANCHA (Recomendado para ahorrar más intereses)*\n\n{avalancha}\n\n"
        f"{'='*25}\n\n"
        f"*❄️ PLAN BOLA DE NIEVE (Recomendado para motivación rápida)*\n\n{bola_nieve}"
    )

    keyboard = [[InlineKeyboardButton("⬅️ Volver a Deudas", callback_data="debt_menu_back")]]
    await update.message.reply_text(texto, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    return DEBT_MENU

# --- Editar Presupuesto ---
async def edit_budget_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    planner = await get_user_planner(update.effective_user.id, context)
    
    texto = "*⚖️ Editar Porcentajes*\n\nActual:\n"
    for cat, pct in planner.budget_percentages.items():
        texto += f"• {cat}: {Decimal(pct) * 100:.0f}%\n"
    texto += "\nEscribe el nuevo % para *Necesidades*."
    await query.edit_message_text(texto, parse_mode=ParseMode.MARKDOWN)
    return EDIT_BUDGET_NEC

async def edit_budget_nec_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    p_nec = await parse_decimal_input(update.message.text)
    if p_nec is None or not (0 <= p_nec <= 100):
        await update.message.reply_text("Porcentaje inválido.")
        return EDIT_BUDGET_NEC
    context.user_data['p_nec'] = p_nec
    await update.message.reply_text(f"Te queda {100 - p_nec}%. ¿Qué % para *Deseos*?", parse_mode=ParseMode.MARKDOWN)
    return EDIT_BUDGET_DES

async def edit_budget_des_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    p_des = await parse_decimal_input(update.message.text)
    p_nec = context.user_data['p_nec']
    restante = 100 - p_nec
    if p_des is None or not (0 <= p_des <= restante):
        await update.message.reply_text(f"Porcentaje inválido (máx {restante}).")
        return EDIT_BUDGET_DES
    
    p_aho = 100 - p_nec - p_des
    new_percentages = {
        "Necesidades": str(p_nec / 100), "Deseos": str(p_des / 100), "Inversión": str(p_aho / 100)
    }
    user_id = str(update.effective_user.id)
    user_ref = db.collection('usuarios').document(user_id)
    try:
        user_ref.update({'budget_percentages': new_percentages})
        invalidate_planner_cache(context)
        await update.message.reply_text("✅ ¡Presupuesto actualizado!")
    except Exception:
        await update.message.reply_text("❌ Lo siento, ocurrió un error al actualizar tus porcentajes. Intenta más tarde.")
    
    context.user_data.clear()
    await main_menu(update, context)
    return ConversationHandler.END

# ============================================================================ #
# SECTION 9: GASTOS RÁPIDOS (Atajos)
# ============================================================================ #

async def expense_quick_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    planner = await get_user_planner(update.effective_user.id, context)
    context.user_data['planner'] = planner
    
    if not planner.gastos_rapidos:
        keyboard = [
            [InlineKeyboardButton("➕ Crear mi primer atajo", callback_data="qcrud_add_start")],
            [InlineKeyboardButton("⬅️ Volver", callback_data="expense_hub")]
        ]
        await query.edit_message_text(
            "No tienes atajos configurados. ¡Crea uno para registrar gastos en un toque!", 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return QUICK_EXPENSE_CRUD_ACTION

    keyboard = [[InlineKeyboardButton(f"{g.nombre} (${g.monto:,.2f})", callback_data=f"qexec_{g.id}")] for g in planner.gastos_rapidos]
    keyboard.append([InlineKeyboardButton("⚙️ Gestionar Atajos", callback_data="quick_expense_menu")])
    keyboard.append([InlineKeyboardButton("⬅️ Volver", callback_data="expense_hub")])
    await query.edit_message_text("⚡ Elige un Gasto Rápido para registrar:", reply_markup=InlineKeyboardMarkup(keyboard))
    return EXPENSE_QUICK_SELECT

async def expense_quick_select_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    gasto_rapido_id = query.data.split('_')[-1]
    planner = context.user_data.get('planner')
    gasto_config = next((g for g in planner.gastos_rapidos if g.id == gasto_rapido_id), None)
    
    if not gasto_config:
        await query.edit_message_text("Error: Atajo no encontrado.")
        await main_menu(update, context)
        return ConversationHandler.END

    transaccion_data = {
        'monto': gasto_config.monto,
        'categoria': gasto_config.categoria,
        'tipo_gasto': gasto_config.tipo_gasto,
        'descripcion': gasto_config.nombre
    }
    return await save_transaction_and_check_overspending(update, context, planner, transaccion_data)

async def quick_expense_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    planner = await get_user_planner(update.effective_user.id, context)

    texto = ["*⚡ Gestionar Gastos Rápidos*"]
    if not planner.gastos_rapidos:
        texto.append("No tienes atajos configurados.")
    else:
        for g in planner.gastos_rapidos:
            texto.append(f"• {g.nombre} (${g.monto:,.2f} - {g.tipo_gasto})")

    keyboard = [
        [InlineKeyboardButton("➕ Añadir", callback_data="qcrud_add")],
        [InlineKeyboardButton("🗑️ Eliminar", callback_data="qcrud_delete")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="edit_profile_menu")]
    ]
    await query.edit_message_text(escape_markdown_v2("\n".join(texto)), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    return QUICK_EXPENSE_CRUD_ACTION

async def quick_expense_crud_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data.split('_')[1]

    if action == "add" or action == "add_start":
        context.user_data['qexp_data'] = {}
        await query.edit_message_text("Nombre para el atajo (ej. 'Café de la mañana'):")
        return QUICK_EXPENSE_ADD_NOMBRE
    elif action == "delete":
        planner = await get_user_planner(update.effective_user.id, context)
        if not planner.gastos_rapidos:
            await query.edit_message_text("No hay atajos para eliminar.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="quick_expense_menu")]]))
            return QUICK_EXPENSE_CRUD_ACTION
        keyboard = [[InlineKeyboardButton(f"{g.nombre} (${g.monto:,.2f})", callback_data=f"del_qexp_{g.id}")] for g in planner.gastos_rapidos]
        keyboard.append([InlineKeyboardButton("Cancelar", callback_data="cancel_op")])
        await query.edit_message_text("Selecciona el atajo a eliminar:", reply_markup=InlineKeyboardMarkup(keyboard))
        return QUICK_EXPENSE_DELETE_SELECT
    return QUICK_EXPENSE_CRUD_ACTION

async def quick_expense_add_nombre_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['qexp_data']['nombre'] = update.message.text
    await update.message.reply_text("¿Cuál es el monto de este gasto?")
    return QUICK_EXPENSE_ADD_MONTO

async def quick_expense_add_monto_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    monto = await parse_decimal_input(update.message.text)
    if monto is None or monto <= 0:
        await update.message.reply_text("Monto inválido.")
        return QUICK_EXPENSE_ADD_MONTO
    context.user_data['qexp_data']['monto'] = monto
    keyboard = [
        [InlineKeyboardButton("Comida", callback_data="cat_Comida"), InlineKeyboardButton("Transporte", callback_data="cat_Transporte")],
        [InlineKeyboardButton("Hogar", callback_data="cat_Hogar"), InlineKeyboardButton("Entretenimiento", callback_data="cat_Entretenimiento")],
        [InlineKeyboardButton("Salud", callback_data="cat_Salud"), InlineKeyboardButton("Otro", callback_data="cat_Otro")],
    ]
    await update.message.reply_text("¿A qué categoría pertenece?", reply_markup=InlineKeyboardMarkup(keyboard))
    return QUICK_EXPENSE_ADD_CATEGORIA

async def quick_expense_add_categoria_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['qexp_data']['categoria'] = query.data.split('_')[1]
    keyboard = [
        [InlineKeyboardButton("Necesidad", callback_data="tipo_Necesidades")],
        [InlineKeyboardButton("Deseo", callback_data="tipo_Deseos")]
    ]
    await query.edit_message_text("¿Es una necesidad o un deseo?", reply_markup=InlineKeyboardMarkup(keyboard))
    return QUICK_EXPENSE_ADD_TIPO

async def quick_expense_add_tipo_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['qexp_data']['tipo_gasto'] = query.data.split('_')[1]
    user_id = str(update.effective_user.id)
    # Guardamos el atajo en Firestore. Si ocurre un error, notificamos al usuario.
    doc_id = await save_document(['usuarios', user_id, 'gastos_rapidos'], context.user_data['qexp_data'])
    if doc_id:
        # Invalidate cache so next planner load reflects the new quick expense
        invalidate_planner_cache(context)
        await query.edit_message_text("✅ ¡Atajo creado con éxito!")
    else:
        await query.edit_message_text("❌ Lo siento, ocurrió un error al guardar el atajo. Inténtalo de nuevo más tarde.")
    await main_menu(update, context)
    return ConversationHandler.END

async def quick_expense_delete_select_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_op":
        await query.edit_message_text("Operación cancelada.")
    else:
        qexp_id = query.data.split('_')[-1]
        user_id = str(update.effective_user.id)
        deleted = await delete_document(['usuarios', user_id, 'gastos_rapidos', qexp_id])
        if deleted:
            invalidate_planner_cache(context)
            await query.edit_message_text("🗑️ Atajo eliminado.")
        else:
            await query.edit_message_text("⚠️ No pude eliminarlo ahora mismo. Intenta nuevamente más tarde.")
    await main_menu(update, context)
    return ConversationHandler.END

# ============================================================================ #
# SECTION 10: PUNTO DE ENTRADA Y CONFIGURACIÓN DEL BOT
# ============================================================================ #

def main() -> None:
    initialize_firebase()
    #initialize_database_content() # Descomentar si es la primera vez que se corre con tips_financieros.json

    # Validamos que el token esté configurado correctamente mediante variables de entorno
    if not TELEGRAM_TOKEN:
        logger.error("FATAL: La variable de entorno TELEGRAM_TOKEN no está definida. Configura el token antes de iniciar el bot.")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()


    income_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(income_menu, pattern='^income_menu$')],
        states={
            INCOME_CRUD_ACTION: [CallbackQueryHandler(income_crud_action, pattern='^income_')],
            INCOME_ADD_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_add_nombre_step)],
            INCOME_ADD_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_add_monto_step)],
            INCOME_DELETE_SELECT: [CallbackQueryHandler(income_delete_select_step, pattern='^del_income_|^cancel_op$')],
        },
        fallbacks=[CallbackQueryHandler(back_to_edit_profile_menu, pattern="^edit_profile_menu$")],
    )
    
    ### CAMBIO: El ConversationHandler de deudas ahora es más complejo y maneja todos los estados CRUD.
    debt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(debt_main_menu, pattern='^debt_menu$')],
        states={
            DEBT_MENU: [
                CallbackQueryHandler(debt_main_menu, pattern='^debt_menu_back$')
            ],
            DEBT_ACTION: [
                CallbackQueryHandler(debt_action_handler, pattern='^debt_')
            ],
            # Flujo de Añadir
            DEBT_ADD_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_nombre_step)],
            DEBT_ADD_SALDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_saldo_step)],
            DEBT_ADD_TASA: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_tasa_step)],
            DEBT_ADD_PAGO: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_pago_step)],
            DEBT_ADD_CONFIRM: [CallbackQueryHandler(debt_add_confirm_step, pattern='^confirm_')],
            # Flujo de Eliminar
            DEBT_DELETE_SELECT: [CallbackQueryHandler(debt_delete_select_step, pattern='^del_|^cancel_debt$')],
            # Flujo de Editar
            DEBT_EDIT_SELECT: [CallbackQueryHandler(debt_edit_select_step, pattern='^edit_|^cancel_debt$')],
            DEBT_EDIT_FIELD: [CallbackQueryHandler(debt_edit_field_step, pattern='^editfield_|^cancel_debt$')],
            DEBT_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_edit_value_step)],
            # Flujo de Plan de Pago
            DEBT_PLAN_EXTRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_plan_extra_step)],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
            CommandHandler('cancelar', cancel)
        ],
    )

    edit_budget_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_budget_start, pattern='^edit_budget_start$')],
        states={
            EDIT_BUDGET_NEC: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_budget_nec_step)],
            EDIT_BUDGET_DES: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_budget_des_step)],
        },
        fallbacks=[CallbackQueryHandler(back_to_edit_profile_menu, pattern="^edit_profile_menu$")],
    )
    
    quick_expense_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(expense_quick_start, pattern='^expense_quick_start$'),
            CallbackQueryHandler(quick_expense_menu, pattern='^quick_expense_menu$')
        ],
        states={
            EXPENSE_QUICK_SELECT: [
                CallbackQueryHandler(expense_quick_select_step, pattern='^qexec_'),
                CallbackQueryHandler(quick_expense_menu, pattern='^quick_expense_menu$')
            ],
            QUICK_EXPENSE_CRUD_ACTION: [CallbackQueryHandler(quick_expense_crud_action, pattern='^qcrud_')],
            QUICK_EXPENSE_ADD_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, quick_expense_add_nombre_step)],
            QUICK_EXPENSE_ADD_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, quick_expense_add_monto_step)],
            QUICK_EXPENSE_ADD_CATEGORIA: [CallbackQueryHandler(quick_expense_add_categoria_step, pattern='^cat_')],
            QUICK_EXPENSE_ADD_TIPO: [CallbackQueryHandler(quick_expense_add_tipo_step, pattern='^tipo_')],
            QUICK_EXPENSE_DELETE_SELECT: [CallbackQueryHandler(quick_expense_delete_select_step, pattern='^del_qexp_|^cancel_op$')],
            OVERSPEND_CHOICE: [CallbackQueryHandler(overspend_choice_step, pattern='^overspend_')],
            OVERSPEND_MOVE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, overspend_move_amount_step)]
        },
        fallbacks=[CommandHandler('cancelar', cancel)],
    )

onboarding_conv = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
    states={
        ONBOARDING_META: [CallbackQueryHandler(onboarding_meta_step, pattern='^meta_')],
        ONBOARDING_INGRESO: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboarding_ingreso_step)],
        ONBOARDING_PLAN: [CallbackQueryHandler(onboarding_plan_step, pattern='^plan_')],
        ONBOARDING_PLAN_CUSTOM_NEC: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboarding_custom_nec_step)],
        ONBOARDING_PLAN_CUSTOM_DES: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboarding_custom_des_step)],
        ONBOARDING_DEUDA_PREGUNTA: [CallbackQueryHandler(onboarding_deuda_pregunta_step, pattern='^deuda_')],
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
)

expense_detailed_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(expense_detailed_start, pattern='^expense_detailed_start$')],
    states={
        EXPENSE_DETAILED_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_detailed_monto_step)],
        EXPENSE_DETAILED_CATEGORIA: [CallbackQueryHandler(expense_detailed_categoria_step, pattern='^cat_')],
        EXPENSE_DETAILED_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, expense_detailed_desc_step)],
        EXPENSE_DETAILED_TIPO: [CallbackQueryHandler(expense_detailed_tipo_step, pattern='^tipo_')],
        OVERSPEND_CHOICE: [CallbackQueryHandler(overspend_choice_step, pattern='^overspend_')],
        OVERSPEND_MOVE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, overspend_move_amount_step)]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    map_to_parent={ ConversationHandler.END: ConversationHandler.END }
)

investment_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(investment_start, pattern='^investment_start$')],
    states={
        INVESTMENT_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, investment_monto_step)],
        INVESTMENT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, investment_desc_step)],
        OVERSPEND_CHOICE: [CallbackQueryHandler(overspend_choice_step, pattern='^overspend_')],
        OVERSPEND_MOVE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, overspend_move_amount_step)]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
)

income_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(income_menu, pattern='^income_menu$')],
    states={
        INCOME_CRUD_ACTION: [CallbackQueryHandler(income_crud_action, pattern='^income_')],
        INCOME_ADD_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_add_nombre_step)],
        INCOME_ADD_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, income_add_monto_step)],
        INCOME_DELETE_SELECT: [CallbackQueryHandler(income_delete_select_step, pattern='^del_income_|^cancel_op$')],
    },
    fallbacks=[CallbackQueryHandler(back_to_edit_profile_menu, pattern="^edit_profile_menu$")],
)

debt_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(debt_main_menu, pattern='^debt_menu$')],
    states={
        DEBT_MENU: [CallbackQueryHandler(debt_main_menu, pattern='^debt_menu_back$')],
        DEBT_ACTION: [CallbackQueryHandler(debt_action_handler, pattern='^debt_')],
        DEBT_ADD_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_nombre_step)],
        DEBT_ADD_SALDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_saldo_step)],
        DEBT_ADD_TASA: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_tasa_step)],
        DEBT_ADD_PAGO: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_add_pago_step)],
        DEBT_ADD_CONFIRM: [CallbackQueryHandler(debt_add_confirm_step, pattern='^confirm_')],
        DEBT_DELETE_SELECT: [CallbackQueryHandler(debt_delete_select_step, pattern='^del_|^cancel_debt$')],
        DEBT_EDIT_SELECT: [CallbackQueryHandler(debt_edit_select_step, pattern='^edit_|^cancel_debt$')],
        DEBT_EDIT_FIELD: [CallbackQueryHandler(debt_edit_field_step, pattern='^editfield_|^cancel_debt$')],
        DEBT_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_edit_value_step)],
        DEBT_PLAN_EXTRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, debt_plan_extra_step)],
    },
    fallbacks=[
        CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
        CommandHandler('cancelar', cancel)
    ],
)

edit_budget_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(edit_budget_start, pattern='^edit_budget_start$')],
    states={
        EDIT_BUDGET_NEC: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_budget_nec_step)],
        EDIT_BUDGET_DES: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_budget_des_step)],
    },
    fallbacks=[CallbackQueryHandler(back_to_edit_profile_menu, pattern="^edit_profile_menu$")],
)

quick_expense_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(expense_quick_start, pattern='^expense_quick_start$'),
        CallbackQueryHandler(quick_expense_menu, pattern='^quick_expense_menu$')
    ],
    states={
        EXPENSE_QUICK_SELECT: [
            CallbackQueryHandler(expense_quick_select_step, pattern='^qexec_'),
            CallbackQueryHandler(quick_expense_menu, pattern='^quick_expense_menu$')
        ],
        QUICK_EXPENSE_CRUD_ACTION: [CallbackQueryHandler(quick_expense_crud_action, pattern='^qcrud_')],
        QUICK_EXPENSE_ADD_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, quick_expense_add_nombre_step)],
        QUICK_EXPENSE_ADD_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, quick_expense_add_monto_step)],
        QUICK_EXPENSE_ADD_CATEGORIA: [CallbackQueryHandler(quick_expense_add_categoria_step, pattern='^cat_')],
        QUICK_EXPENSE_ADD_TIPO: [CallbackQueryHandler(quick_expense_add_tipo_step, pattern='^tipo_')],
        QUICK_EXPENSE_DELETE_SELECT: [CallbackQueryHandler(quick_expense_delete_select_step, pattern='^del_qexp_|^cancel_op$')],
        OVERSPEND_CHOICE: [CallbackQueryHandler(overspend_choice_step, pattern='^overspend_')],
        OVERSPEND_MOVE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, overspend_move_amount_step)]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
)
# === Fin bloque movido ===

# --- al final de bot.py, antes del if __name__ == "__main__" ---

def build_application() -> Application:
    initialize_firebase()
    # initialize_database_content()  # si necesitas poblar tips la 1a vez

    if not TELEGRAM_TOKEN or "REEMPLAZAR" in TELEGRAM_TOKEN:
        raise RuntimeError("Token de Telegram no configurado.")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers (copiamos el mismo bloque que hoy está dentro de main())
    application.add_handler(onboarding_conv)
    application.add_handler(debt_conv) 
    application.add_handler(income_conv)
    application.add_handler(edit_budget_conv)
    application.add_handler(quick_expense_conv)
    application.add_handler(expense_detailed_conv)
    application.add_handler(investment_conv)

    application.add_handler(CommandHandler("menu", main_menu))
    application.add_handler(CommandHandler("cancelar", cancel))
    application.add_handler(CallbackQueryHandler(main_menu, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(get_tip, pattern='^get_tip$'))
    application.add_handler(CallbackQueryHandler(full_report, pattern='^full_report$'))
    application.add_handler(CallbackQueryHandler(edit_profile_menu, pattern='^edit_profile_menu$'))
    application.add_handler(CallbackQueryHandler(expense_hub, pattern='^expense_hub$'))

    return application

if __name__ == "__main__":
    # Modo local/personal: polling (no se usa en Render)
    app = build_application()
    logger.info("Iniciando bot (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

