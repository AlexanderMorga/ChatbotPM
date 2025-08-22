# financial_planner_v4.py
# Este script requiere la instalación de numpy-financial.
# Ejecuta: pip install numpy-financial
import random
import json
from datetime import datetime
import os
import numpy_financial as npf
from pathlib import Path

# Carpeta donde vive este script (.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Define la carpeta donde se guardará el archivo JSON (usa la del script)
DIRECTORIO_DATOS = BASE_DIR
NOMBRE_ARCHIVO = "financial_data.json"

# Se asegura de que el directorio exista, si no, lo crea.
if not os.path.exists(DIRECTORIO_DATOS):
    os.makedirs(DIRECTORIO_DATOS)

RUTA_COMPLETA_ARCHIVO = os.path.join(DIRECTORIO_DATOS, NOMBRE_ARCHIVO)

# --- CONFIGURACIÓN DE TIPS FINANCIEROS ---
ARCHIVO_TIPS = os.path.join(DIRECTORIO_DATOS, "tips_financieros.json")


# ----------------------------------------------------------------------------
# SECTION 1: CLASES DE DATOS
# ----------------------------------------------------------------------------

class Ingreso:
    def __init__(self, nombre, monto, **kwargs):
        self.nombre = nombre
        self.monto = float(monto)
    def to_dict(self): return self.__dict__
    @classmethod
    def from_dict(cls, data): return cls(**data)

class PresupuestoCategoria:
    def __init__(self, nombre, monto_asignado):
        self.nombre = nombre
        self.monto_asignado = float(monto_asignado)
    def to_dict(self): return self.__dict__
    @classmethod
    def from_dict(cls, data): return cls(**data)

class Transaccion:
    def __init__(self, monto, categoria, tipo_gasto, descripcion="", fecha=None):
        self.monto = float(monto)
        self.categoria = categoria
        self.tipo_gasto = tipo_gasto
        self.descripcion = descripcion
        self.fecha = fecha or datetime.now().strftime("%Y-%m-%d")
    def to_dict(self): return self.__dict__
    @classmethod
    def from_dict(cls, data): return cls(**data)

class DeudaEstrategia:
    def __init__(self, nombre, saldo_actual, tasa_interes_anual, pago_minimo_mensual):
        self.nombre = nombre
        self.saldo_actual = float(saldo_actual)
        self.tasa_interes_anual = float(tasa_interes_anual)
        self.pago_minimo_mensual = float(pago_minimo_mensual)
    def to_dict(self): return self.__dict__
    @classmethod
    def from_dict(cls, data): return cls(**data)

# ----------------------------------------------------------------------------
# SECTION 1.5: MANEJADOR DE TIPS
# ----------------------------------------------------------------------------

class TipManager:
    def __init__(self, ruta_json):
        self.ruta_json = ruta_json
        self.tips = []
        self._cargar()

    def _cargar(self):
        if os.path.exists(self.ruta_json):
            with open(self.ruta_json, "r", encoding="utf-8") as f:
                self.tips = json.load(f)
        else:
            self.tips = []

    def filtrar(self, nivel:str, condicion:str, excluidos:set):
        candidatos = []
        for tip in self.tips:
            niveles = tip.get("nivel_ingreso", [])
            condiciones = tip.get("condicion", [])
            if (nivel in niveles or "Todos" in niveles) and (condicion in condiciones):
                if tip.get("id") not in excluidos:
                    candidatos.append(tip)
        return candidatos

    def elegir_uno(self, nivel:str, condicion:str, excluidos:set):
        lista = self.filtrar(nivel, condicion, excluidos)
        if not lista:
            return None
        return random.choice(lista)


# ----------------------------------------------------------------------------
# SECTION 2: CLASE PRINCIPAL DEL PLANIFICADOR
# ----------------------------------------------------------------------------

class PlanificadorFinanciero:
    def __init__(self, archivo_datos):
        self.archivo_datos = archivo_datos
        self._inicializar_datos()
        self.cargar_datos()
        self.tip_manager = TipManager(ARCHIVO_TIPS)

    def _inicializar_datos(self):
        self.ingresos = []
        self.presupuestos = []
        self.transacciones = []
        self.deudas_estrategia = []
        self.meta_principal = ""
        self.sobregiros_mes_actual = {}
        self.porcentaje_deuda_ingreso = None
        self.tips_mostrados_ids = []
        self.budget_percentages = {"Necesidades": 0.5, "Deseos": 0.3, "Ahorro/Deudas": 0.2}

    def agregar_ingreso(self, ingreso): self.ingresos.append(ingreso)
    def agregar_presupuesto(self, presupuesto): self.presupuestos.append(presupuesto)
    def agregar_transaccion(self, transaccion): self.transacciones.append(transaccion)
    def agregar_deuda_estrategia(self, deuda): self.deudas_estrategia.append(deuda)

    def _ingreso_mensual_total(self):
        return sum(i.monto for i in self.ingresos)

    def nivel_por_ingreso(self):
        total = self._ingreso_mensual_total()
        if total < 9000: return "Nivel 1"
        if total < 30000: return "Nivel 2"
        if total < 80000: return "Nivel 3"
        if total < 150000: return "Nivel 4"
        return "Nivel 5"

    def condicion_por_deuda(self):
        if self.porcentaje_deuda_ingreso is not None:
            return "Con deudas" if self.porcentaje_deuda_ingreso > 0 else "Sin deudas"
        saldo = sum(d.saldo_actual for d in self.deudas_estrategia)
        return "Con deudas" if saldo > 0 else "Sin deudas"

    def siguiente_tip(self):
        nivel = self.nivel_por_ingreso()
        condicion = self.condicion_por_deuda()
        excluidos = set(self.tips_mostrados_ids)
        tip = self.tip_manager.elegir_uno(nivel, condicion, excluidos)
        if tip is None:
            self.tips_mostrados_ids = []
            excluidos = set()
            tip = self.tip_manager.elegir_uno(nivel, condicion, excluidos)
        if tip:
            self.tips_mostrados_ids.append(tip["id"])
            self.guardar_datos()
        return tip

    def get_presupuesto_por_nombre(self, nombre):
        for p in self.presupuestos:
            if p.nombre == nombre:
                return p
        return None
        
    def recalcular_presupuestos(self):
        """Recalcula los montos de presupuesto basados en el ingreso total y los porcentajes guardados."""
        ingreso_total = self._ingreso_mensual_total()
        for nombre, porcentaje in self.budget_percentages.items():
            presupuesto_obj = self.get_presupuesto_por_nombre(nombre)
            if presupuesto_obj:
                presupuesto_obj.monto_asignado = ingreso_total * porcentaje
        print("\n✅ Presupuestos recalculados con éxito según tu nuevo ingreso/porcentajes.")

    def calcular_gastos_reales_por_tipo(self, mes, anio):
        gastos = {"Necesidades": 0, "Deseos": 0, "Ahorro/Deudas": 0}
        transacciones_mes = [
            t for t in self.transacciones 
            if datetime.strptime(t.fecha, "%Y-%m-%d").month == mes and 
               datetime.strptime(t.fecha, "%Y-%m-%d").year == anio
        ]
        for t in transacciones_mes:
            if t.tipo_gasto in gastos:
                gastos[t.tipo_gasto] += t.monto
        return gastos

    def generar_plan_avalancha(self, dinero_extra_mensual):
        deudas_ordenadas = sorted(self.deudas_estrategia, key=lambda x: x.tasa_interes_anual, reverse=True)
        return self._generar_plan_pago_deuda(deudas_ordenadas, dinero_extra_mensual)

    def generar_plan_bola_de_nieve(self, dinero_extra_mensual):
        deudas_ordenadas = sorted(self.deudas_estrategia, key=lambda x: x.saldo_actual)
        return self._generar_plan_pago_deuda(deudas_ordenadas, dinero_extra_mensual)

    def _generar_plan_pago_deuda(self, deudas_ordenadas, dinero_extra_mensual):
        if not deudas_ordenadas: return "No hay deudas registradas para generar un plan."
        plan = []
        total_pagos_minimos = sum(d.pago_minimo_mensual for d in deudas_ordenadas)
        plan.append(f"1. Paga el pago mínimo en TODAS tus deudas (${total_pagos_minimos:,.2f} al mes).")
        plan.append(f"2. Usa tu dinero extra mensual (${dinero_extra_mensual:,.2f}) para atacar la primera deuda de la lista.")
        for i, deuda in enumerate(deudas_ordenadas):
            plan.append(f"\nPrioridad #{i+1}: {deuda.nombre} (Saldo: ${deuda.saldo_actual:,.2f}, Tasa: {deuda.tasa_interes_anual:.2%})")
        plan.append("\n3. Al liquidar una deuda, suma su pago mínimo al dinero extra y ataca la siguiente.")
        return "\n".join(plan)

    def guardar_datos(self):
        datos = {
            "meta_principal": self.meta_principal,
            "ingresos": [i.to_dict() for i in self.ingresos],
            "presupuestos": [p.to_dict() for p in self.presupuestos],
            "transacciones": [t.to_dict() for t in self.transacciones],
            "deudas_estrategia": [d.to_dict() for d in self.deudas_estrategia],
            "sobregiros_mes_actual": self.sobregiros_mes_actual,
            "porcentaje_deuda_ingreso": self.porcentaje_deuda_ingreso,
            "tips_mostrados_ids": self.tips_mostrados_ids,
            "budget_percentages": self.budget_percentages
        }
        with open(self.archivo_datos, 'w', encoding='utf-8') as f:
            json.dump(datos, f, indent=4, ensure_ascii=False)

    def cargar_datos(self):
        if not os.path.exists(self.archivo_datos): return
        with open(self.archivo_datos, 'r', encoding='utf-8') as f:
            datos = json.load(f)
        self.meta_principal = datos.get("meta_principal", "")
        self.ingresos = [Ingreso.from_dict(i) for i in datos.get("ingresos", [])]
        self.presupuestos = [PresupuestoCategoria.from_dict(p) for p in datos.get("presupuestos", [])]
        self.transacciones = [Transaccion.from_dict(t) for t in datos.get("transacciones", [])]
        self.deudas_estrategia = [DeudaEstrategia.from_dict(d) for d in datos.get("deudas_estrategia", [])]
        self.sobregiros_mes_actual = datos.get("sobregiros_mes_actual", {})
        self.porcentaje_deuda_ingreso = datos.get("porcentaje_deuda_ingreso", None)
        self.tips_mostrados_ids = datos.get("tips_mostrados_ids", [])
        self.budget_percentages = datos.get("budget_percentages", {"Necesidades": 0.5, "Deseos": 0.3, "Ahorro/Deudas": 0.2})
        print(f"✅ Perfil financiero cargado desde '{self.archivo_datos}'.")

# ----------------------------------------------------------------------------
# SECTION 3: FUNCIONES AUXILIARES DE INTERFAZ
# ----------------------------------------------------------------------------

def _ask_with_options(prompt, options):
    print(f"\n{prompt}")
    for i, option in enumerate(options, 1):
        print(f"{i}. {option}")
    while True:
        try:
            choice = int(input("Elige una opción: "))
            if 1 <= choice <= len(options):
                return options[choice - 1]
            else:
                print("Opción fuera de rango.")
        except ValueError:
            print("Por favor, ingresa un número.")

def _ask_for_float_with_confirmation(prompt, default_value=None):
    display_prompt = f"{prompt} (actual: {default_value or 'N/A'}): " if default_value else f"{prompt}: "
    while True:
        try:
            value_str = input(display_prompt)
            if not value_str and default_value is not None:
                return default_value
            value = float(value_str)
            if _ask_yes_or_no(f"Recibido: ${value:,.2f}. ¿Es correcto?"):
                return value
        except ValueError:
            print("Por favor, ingresa un número válido.")

def _ask_yes_or_no(prompt):
    while True:
        answer = input(f"{prompt} (s/n): ").lower()
        if answer in ['s', 'si']: return True
        if answer in ['n', 'no']: return False
        print("Respuesta no válida. Por favor, responde 's' o 'n'.")

# ----------------------------------------------------------------------------
# SECTION 4: FLUJOS DE USUARIO GUIADOS
# ----------------------------------------------------------------------------

def onboarding_wizard(planner):
    print("--- 🚀 ¡Hola! Soy tu asistente financiero. Vamos a configurar tu perfil. ---")
    
    meta_options = ["Pagar mis deudas", "Ahorrar para una meta", "Empezar a invertir", "Solo entender mis gastos"]
    planner.meta_principal = _ask_with_options("Para empezar, ¿qué es lo más importante que quieres lograr?", meta_options)

    print("\n¡Excelente meta! Para ayudarte a lograrlo, necesito saber tu ingreso.")
    ingreso_total = _ask_for_float_with_confirmation("¿Cuál es tu ingreso mensual total después de impuestos?")
    planner.agregar_ingreso(Ingreso("Ingreso Principal", ingreso_total))

    print("\n¡Perfecto! Basado en tus ingresos, este es un plan de gastos recomendado (50/30/20):")
    necesidades = ingreso_total * 0.5
    deseos = ingreso_total * 0.3
    ahorro = ingreso_total * 0.2
    print(f"- Necesidades (50%): ${necesidades:,.2f} al mes")
    print(f"- Deseos (30%):      ${deseos:,.2f} al mes")
    print(f"- Ahorro/Deudas (20%):${ahorro:,.2f} al mes")
    
    if _ask_yes_or_no("\n¿Te parece bien este plan para comenzar?"):
        planner.agregar_presupuesto(PresupuestoCategoria("Necesidades", necesidades))
        planner.agregar_presupuesto(PresupuestoCategoria("Deseos", deseos))
        planner.agregar_presupuesto(PresupuestoCategoria("Ahorro/Deudas", ahorro))
        print("\n✅ ¡Genial! Tu perfil está configurado.")
    else:
        print("\nEntendido. Por ahora, usaremos este plan como base.")
        planner.agregar_presupuesto(PresupuestoCategoria("Necesidades", necesidades))
        planner.agregar_presupuesto(PresupuestoCategoria("Deseos", deseos))
        planner.agregar_presupuesto(PresupuestoCategoria("Ahorro/Deudas", ahorro))

    if _ask_yes_or_no("\n¿Destinas actualmente parte de tu ingreso al pago de deudas?"):
        while True:
            try:
                pct = float(input("¿Qué porcentaje aproximado? (ej. 25 para 25%): "))
                if 0 <= pct <= 100:
                    planner.porcentaje_deuda_ingreso = pct
                    break
                else:
                    print("Ingresa un porcentaje entre 0 y 100.")
            except ValueError:
                print("Por favor, ingresa un número válido.")
    else:
        planner.porcentaje_deuda_ingreso = 0.0

def daily_expense_wizard(planner):
    # (Sin cambios en esta función)
    pass

def investment_contribution_wizard(planner):
    # (Sin cambios en esta función)
    pass

def manage_debt_strategies(planner):
    # (Sin cambios en esta función)
    pass

def show_full_report(planner):
    # (Sin cambios en esta función)
    pass

def edit_profile_wizard(planner):
    """Nuevo Flujo: Asistente para editar el perfil financiero."""
    while True:
        print("\n--- 🛠️ Editar Perfil Financiero ---")
        opcion = _ask_with_options("¿Qué te gustaría editar?", [
            "Editar Ingresos",
            "Editar Porcentajes del Presupuesto",
            "Editar Deudas",
            "Volver al Menú Principal"
        ])

        if opcion == "Editar Ingresos":
            if not planner.ingresos:
                print("Aún no has registrado ningún ingreso.")
                continue
            
            ingreso_a_editar = planner.ingresos[0] # Asumimos un solo ingreso principal por ahora
            print(f"\nTu ingreso principal actual es de ${ingreso_a_editar.monto:,.2f}.")
            nuevo_monto = _ask_for_float_with_confirmation("Ingresa el nuevo monto mensual")
            ingreso_a_editar.monto = nuevo_monto
            planner.recalcular_presupuestos()
            planner.guardar_datos()

        elif opcion == "Editar Porcentajes del Presupuesto":
            print("\nTu distribución actual es:")
            for cat, pct in planner.budget_percentages.items():
                print(f"- {cat}: {pct:.0%}")
            
            if not _ask_yes_or_no("¿Deseas cambiarla?"):
                continue

            while True:
                try:
                    p_nec = float(input("Nuevo % para Necesidades (ej. 55): "))
                    p_des = float(input("Nuevo % para Deseos (ej. 25): "))
                    p_aho = float(input("Nuevo % para Ahorro/Deudas (ej. 20): "))
                    if p_nec + p_des + p_aho == 100:
                        planner.budget_percentages["Necesidades"] = p_nec / 100
                        planner.budget_percentages["Deseos"] = p_des / 100
                        planner.budget_percentages["Ahorro/Deudas"] = p_aho / 100
                        planner.recalcular_presupuestos()
                        planner.guardar_datos()
                        break
                    else:
                        print("Error: Los porcentajes deben sumar 100. Inténtalo de nuevo.")
                except ValueError:
                    print("Por favor, ingresa números válidos.")

        elif opcion == "Editar Deudas":
            if not planner.deudas_estrategia:
                print("\nNo tienes deudas registradas para editar.")
                continue
            
            nombres_deudas = [d.nombre for d in planner.deudas_estrategia]
            deuda_a_editar_nombre = _ask_with_options("¿Qué deuda quieres editar?", nombres_deudas)
            deuda_a_editar = next(d for d in planner.deudas_estrategia if d.nombre == deuda_a_editar_nombre)

            que_editar = _ask_with_options(f"Editando '{deuda_a_editar.nombre}'. ¿Qué quieres cambiar?", 
                                           ["Saldo Actual", "Tasa de Interés", "Pago Mínimo", "Eliminar Deuda", "Cancelar"])
            
            if que_editar == "Saldo Actual":
                deuda_a_editar.saldo_actual = _ask_for_float_with_confirmation("Nuevo saldo actual", deuda_a_editar.saldo_actual)
            elif que_editar == "Tasa de Interés":
                nueva_tasa_pct = _ask_for_float_with_confirmation("Nueva tasa de interés anual (%)", deuda_a_editar.tasa_interes_anual * 100)
                deuda_a_editar.tasa_interes_anual = nueva_tasa_pct / 100
            elif que_editar == "Pago Mínimo":
                deuda_a_editar.pago_minimo_mensual = _ask_for_float_with_confirmation("Nuevo pago mínimo mensual", deuda_a_editar.pago_minimo_mensual)
            elif que_editar == "Eliminar Deuda":
                if _ask_yes_or_no(f"¿Estás seguro de que quieres eliminar la deuda '{deuda_a_editar.nombre}'?"):
                    planner.deudas_estrategia.remove(deuda_a_editar)
                    print("Deuda eliminada.")
            
            planner.guardar_datos()

        elif opcion == "Volver al Menú Principal":
            break


def main_menu_loop(planner):
    while True:
        print("\n" + "="*25)
        print("--- MENÚ PRINCIPAL ---")
        print("="*25)
        now = datetime.now()
        gastos_reales = planner.calcular_gastos_reales_por_tipo(now.month, now.year)
        
        print("Estado del Mes:")
        for categoria in ["Necesidades", "Deseos", "Ahorro/Deudas"]:
            presupuesto = planner.get_presupuesto_por_nombre(categoria)
            if presupuesto:
                gastado = gastos_reales.get(categoria, 0)
                print(f"  - {categoria+':':<15} ${gastado:,.2f} / ${presupuesto.monto_asignado:,.2f}")
        
        if planner.sobregiros_mes_actual:
            print("\n⚠️ Recordatorio de Sobregiros Pendientes:")
            for cat, monto in planner.sobregiros_mes_actual.items():
                print(f"  - Tuviste un sobregiro de ${monto:,.2f} en '{cat}'.")

        opcion = _ask_with_options("\n¿Qué quieres hacer?", [
            "Registrar un gasto (Necesidad/Deseo)",
            "Registrar una aportación (Ahorro/Inversión)",
            "Gestionar estrategias de deudas",
            "Ver reporte financiero completo",
            "Dame un tip financiero",
            "Editar mi perfil financiero", # Nueva opción
            "Guardar y Salir"
        ])
        
        if opcion == "Registrar un gasto (Necesidad/Deseo)":
            daily_expense_wizard(planner)
        elif opcion == "Registrar una aportación (Ahorro/Inversión)":
            investment_contribution_wizard(planner)
        elif opcion == "Gestionar estrategias de deudas":
            manage_debt_strategies(planner)
        elif opcion == "Ver reporte financiero completo":
            show_full_report(planner)
        elif opcion == "Dame un tip financiero":
            tip = planner.siguiente_tip()
            if not tip:
                print("\n🔁 Error al cargar tips.")
            else:
                print("\n--- 💡 TIP FINANCIERO ---")
                print(f"{tip.get('titulo')}")
                print(f"{tip.get('explicacion')}")
                print("Presiona ENTER para continuar...")
                input()
        elif opcion == "Editar mi perfil financiero":
            edit_profile_wizard(planner)
        elif opcion == "Guardar y Salir":
            planner.guardar_datos()
            print("\n✅ Datos guardados. ¡Hasta luego!")
            break

# ----------------------------------------------------------------------------
# SECTION 5: PUNTO DE ENTRADA PRINCIPAL
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    # Rellenar las funciones vacías con la lógica de la versión anterior
    def daily_expense_wizard(planner):
        print("\n--- 💸 Registrar un Gasto ---")
        while True:
            monto = _ask_for_float_with_confirmation("Ok, ¿cuánto gastaste?")
            categoria_options = ["Comida", "Transporte", "Hogar", "Entretenimiento", "Salud", "Otro"]
            categoria = _ask_with_options("¿A qué categoría pertenece?", categoria_options)
            tipo_gasto_options = ["Necesidad (supermercado, servicios)", "Deseo (restaurante, cine)"]
            tipo_gasto_raw = _ask_with_options("¿Fue para una necesidad o un deseo?", tipo_gasto_options)
            tipo_gasto = "Necesidades" if "Necesidad" in tipo_gasto_raw else "Deseos"
            planner.agregar_transaccion(Transaccion(monto, categoria, tipo_gasto))
            now = datetime.now()
            gastos_reales = planner.calcular_gastos_reales_por_tipo(now.month, now.year)
            presupuesto_actual = planner.get_presupuesto_por_nombre(tipo_gasto)
            if presupuesto_actual:
                restante = presupuesto_actual.monto_asignado - gastos_reales.get(tipo_gasto, 0)
                print(f"\n¡Listo! Gasto registrado. Te quedan ${restante:,.2f} en tu presupuesto de '{tipo_gasto}'.")
                if restante < 0:
                    handle_overspending_wizard(planner, tipo_gasto, abs(restante))
            planner.guardar_datos()
            if not _ask_yes_or_no("\n¿Quieres registrar otro gasto?"):
                break

    def investment_contribution_wizard(planner):
        print("\n--- 🌱 Registrar Aportación (Ahorro/Inversión/Deuda) ---")
        monto = _ask_for_float_with_confirmation("¿De cuánto fue tu aportación?")
        descripcion = input("Describe brevemente esta aportación (ej. Ahorro para viaje, Pago a Tarjeta): ")
        planner.agregar_transaccion(Transaccion(monto, "Aportación", "Ahorro/Deudas", descripcion))
        planner.guardar_datos()
        now = datetime.now()
        gastos_reales = planner.calcular_gastos_reales_por_tipo(now.month, now.year)
        presupuesto_ahorro = planner.get_presupuesto_por_nombre("Ahorro/Deudas")
        if presupuesto_ahorro:
            aportado = gastos_reales.get("Ahorro/Deudas", 0)
            print(f"\n¡Excelente! Aportación registrada. Este mes has destinado ${aportado:,.2f} de tu meta de ${presupuesto_ahorro.monto_asignado:,.2f} para Ahorro/Deudas.")

    def manage_debt_strategies(planner):
        print("\n--- 💳 Estrategias para Salir de Deudas ---")
        while True:
            opcion = _ask_with_options("¿Qué deseas hacer?", ["Añadir una deuda", "Ver deudas y generar plan", "Volver al menú"])
            if opcion == "Añadir una deuda":
                nombre = input("Nombre del crédito: ")
                saldo = _ask_for_float_with_confirmation("Saldo actual de la deuda")
                tasa = _ask_for_float_with_confirmation("Tasa de interés anual (ej. 25 para 25%)") / 100.0
                pago_minimo = _ask_for_float_with_confirmation("Pago mínimo mensual")
                planner.agregar_deuda_estrategia(DeudaEstrategia(nombre, saldo, tasa, pago_minimo))
                planner.guardar_datos()
                print("✅ Deuda añadida.")
            elif opcion == "Ver deudas y generar plan":
                if not planner.deudas_estrategia:
                    print("\nNo hay deudas registradas. Añade una primero.")
                    continue
                print("\n--- Deudas Registradas ---")
                for d in planner.deudas_estrategia:
                    print(f"- {d.nombre}: Saldo ${d.saldo_actual:,.2f}, Tasa {d.tasa_interes_anual:.2%}")
                extra = _ask_for_float_with_confirmation("\n¿Cuánto dinero extra (adicional a los pagos mínimos) puedes destinar a tus deudas cada mes?")
                print("\n--- 🚀 PLAN AVALANCHA (Ahorra más en intereses) ---")
                print(planner.generar_plan_avalancha(extra))
                print("\n--- ❄️ PLAN BOLA DE NIEVE (Motivación rápida) ---")
                print(planner.generar_plan_bola_de_nieve(extra))
            else:
                break

    def show_full_report(planner):
        print("\n\n--- 📊 REPORTE FINANCIERO MENSUAL 📊 ---")
        now = datetime.now()
        total_ingresos = sum(i.monto for i in planner.ingresos)
        gastos_reales = planner.calcular_gastos_reales_por_tipo(now.month, now.year)
        print(f"\nMes: {now.strftime('%B %Y')}")
        print(f"Ingreso Total Presupuestado: ${total_ingresos:,.2f}")
        print("-" * 40)
        for p in planner.presupuestos:
            gastado = gastos_reales.get(p.nombre, 0)
            restante = p.monto_asignado - gastado
            print(f"Categoría: {p.nombre}")
            print(f"  - Presupuesto: ${p.monto_asignado:,.2f}")
            print(f"  - Registrado:  ${gastado:,.2f}")
            print(f"  - Restante:    ${restante:,.2f}")
        print("-" * 40)
        total_gastado = gastos_reales.get("Necesidades", 0) + gastos_reales.get("Deseos", 0)
        neto = total_ingresos - total_gastado - gastos_reales.get("Ahorro/Deudas", 0)
        print(f"Total Gastado (Necesidades + Deseos): ${total_gastado:,.2f}")
        print(f"Balance Neto (Ingresos - Gastos - Aportaciones): ${neto:,.2f}")

    def handle_overspending_wizard(planner, categoria_excedida, sobregiro):
        print(f"\n¡Atención! 📉 Con este gasto, has excedido tu presupuesto de '{categoria_excedida}' por ${sobregiro:,.2f}.")
        now = datetime.now()
        gastos_reales = planner.calcular_gastos_reales_por_tipo(now.month, now.year)
        opciones_reajuste = []
        for p in planner.presupuestos:
            if p.nombre != categoria_excedida:
                restante = p.monto_asignado - gastos_reales.get(p.nombre, 0)
                if restante > 0:
                    opciones_reajuste.append(f"Mover de '{p.nombre}' (disponible: ${restante:,.2f})")
        if not opciones_reajuste:
            print("No tienes fondos disponibles en otras categorías para cubrir este gasto.")
            planner.sobregiros_mes_actual[categoria_excedida] = sobregiro
            return
        opciones_reajuste.append("Dejarlo así por ahora (se registrará como sobregiro)")
        decision = _ask_with_options("Para mantener tus finanzas en orden, ¿cómo quieres cubrir este monto extra?", opciones_reajuste)
        if "Dejarlo así" in decision:
            planner.sobregiros_mes_actual[categoria_excedida] = sobregiro
            print("Ok, se ha registrado el sobregiro. Lo verás como un recordatorio en el menú principal.")
            return
        categoria_origen = decision.split("'")[1]
        presupuesto_origen = planner.get_presupuesto_por_nombre(categoria_origen)
        disponible_origen = presupuesto_origen.monto_asignado - gastos_reales.get(categoria_origen, 0)
        print(f"\nVas a mover fondos de '{categoria_origen}'. Tienes ${disponible_origen:,.2f} disponibles.")
        monto_a_mover = _ask_for_float_with_confirmation(f"¿Cuánto quieres mover para cubrir el sobregiro de ${sobregiro:,.2f}?")
        if monto_a_mover > disponible_origen:
            print("No puedes mover más dinero del que tienes disponible en esa categoría.")
            planner.sobregiros_mes_actual[categoria_excedida] = sobregiro
        else:
            planner.reajustar_presupuesto(categoria_origen, categoria_excedida, monto_a_mover)
            print(f"\n✅ Presupuesto reajustado. Se movieron ${monto_a_mover:,.2f} de '{categoria_origen}' a '{categoria_excedida}'.")
            sobregiro_restante = sobregiro - monto_a_mover
            if sobregiro_restante > 0:
                planner.sobregiros_mes_actual[categoria_excedida] = sobregiro_restante
                print(f"Aún tienes un sobregiro pendiente de ${sobregiro_restante:,.2f} en '{categoria_excedida}'.")
            elif categoria_excedida in planner.sobregiros_mes_actual:
                del planner.sobregiros_mes_actual[categoria_excedida]

    planner = PlanificadorFinanciero(RUTA_COMPLETA_ARCHIVO)

    if not planner.ingresos and not planner.presupuestos:
        onboarding_wizard(planner)
        planner.guardar_datos()
        print(f"💾 Tu perfil ha sido guardado en '{planner.archivo_datos}'.")

    main_menu_loop(planner)
