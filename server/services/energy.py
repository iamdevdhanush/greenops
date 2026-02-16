"""
GreenOps Energy Calculation Service
Explainable energy waste estimation with documented formulas
"""
from decimal import Decimal
from server.config import config
import logging

logger = logging.getLogger(__name__)

class EnergyService:
    """
    Energy waste calculation based on real-world power consumption estimates.
    
    Power Consumption Model:
    - Idle PC: ~65W (monitor in low power, CPU idle, disks spun down)
    - Active PC: ~120W (typical desktop usage)
    
    Formula:
    energy_wasted_kwh = (idle_seconds / 3600) * (idle_power_watts / 1000)
    
    Assumptions:
    - Desktop PCs (not laptops)
    - Modern hardware (2015+)
    - Standard office monitors
    - No high-power GPUs
    
    Cost Estimation:
    cost_usd = energy_wasted_kwh * electricity_cost_per_kwh
    """
    
    @staticmethod
    def calculate_idle_energy_waste(idle_seconds: int) -> Decimal:
        """
        Calculate energy wasted during idle time.
        
        Args:
            idle_seconds: Number of seconds machine was idle
            
        Returns:
            Energy wasted in kWh
            
        Formula:
            idle_hours = idle_seconds / 3600
            energy_kwh = idle_hours * (IDLE_POWER_WATTS / 1000)
        """
        if idle_seconds < 0:
            logger.warning(f"Negative idle_seconds received: {idle_seconds}")
            return Decimal('0.0')
        
        # Convert seconds to hours
        idle_hours = Decimal(idle_seconds) / Decimal('3600')
        
        # Calculate energy in kWh
        # Power (watts) / 1000 = power in kilowatts
        # kilowatts * hours = kilowatt-hours (kWh)
        idle_power_kw = Decimal(config.IDLE_POWER_WATTS) / Decimal('1000')
        energy_kwh = idle_hours * idle_power_kw
        
        logger.debug(f"Calculated energy waste: {idle_seconds}s = {energy_kwh} kWh")
        return energy_kwh.quantize(Decimal('0.001'))  # 3 decimal places
    
    @staticmethod
    def calculate_cost(energy_kwh: Decimal) -> Decimal:
        """
        Calculate cost of wasted energy.
        
        Args:
            energy_kwh: Energy in kilowatt-hours
            
        Returns:
            Cost in USD
        """
        cost = energy_kwh * Decimal(config.ELECTRICITY_COST_PER_KWH)
        return cost.quantize(Decimal('0.01'))  # 2 decimal places (cents)
    
    @staticmethod
    def estimate_co2_emissions(energy_kwh: Decimal) -> Decimal:
        """
        Estimate CO2 emissions from wasted energy.
        
        Args:
            energy_kwh: Energy in kilowatt-hours
            
        Returns:
            CO2 emissions in kg
            
        Note:
            Uses US average of 0.42 kg CO2 per kWh (EPA 2023)
            This varies by region and energy source
        """
        # US average: 0.42 kg CO2 per kWh
        co2_per_kwh = Decimal('0.42')
        co2_kg = energy_kwh * co2_per_kwh
        return co2_kg.quantize(Decimal('0.001'))
    
    @staticmethod
    def calculate_potential_savings(total_idle_seconds: int, total_machines: int) -> dict:
        """
        Calculate organization-wide potential savings.
        
        Args:
            total_idle_seconds: Total idle time across all machines
            total_machines: Number of machines monitored
            
        Returns:
            Dictionary with savings estimates
        """
        energy_wasted_kwh = EnergyService.calculate_idle_energy_waste(total_idle_seconds)
        cost_wasted = EnergyService.calculate_cost(energy_wasted_kwh)
        co2_emissions_kg = EnergyService.estimate_co2_emissions(energy_wasted_kwh)
        
        # Calculate average per machine
        avg_idle_hours = (Decimal(total_idle_seconds) / Decimal('3600') / 
                         Decimal(total_machines) if total_machines > 0 else Decimal('0'))
        
        return {
            'total_energy_wasted_kwh': float(energy_wasted_kwh),
            'total_cost_usd': float(cost_wasted),
            'total_co2_kg': float(co2_emissions_kg),
            'avg_idle_hours_per_machine': float(avg_idle_hours.quantize(Decimal('0.1'))),
            'total_machines': total_machines,
            'electricity_rate_per_kwh': config.ELECTRICITY_COST_PER_KWH
        }
