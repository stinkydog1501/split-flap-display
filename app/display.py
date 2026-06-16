import utime
import urandom
from machine import I2C, Pin
import micropython
from micropython import const

from splitflap_module import SplitFlapModule

MAX_MODULES = const(8)
MAX_RPM = const(15)

class SplitFlapDisplay:
    def __init__(self, settings):
        self.settings = settings
        self.i2c = None
        self.mqtt = None
        self.modules = []

    def init(self):
        if I2C is None or Pin is None:
            raise RuntimeError("machine.I2C is required on the target board")

        self.num_modules = self.settings.get_int("moduleCount")
        self.steps_per_rot = self.settings.get_int("stepsPerRot")
        self.display_offset = self.settings.get_int("displayOffset")
        self.magnet_position = self.settings.get_int("magnetPosition")
        self.max_vel = self.settings.get_int("maxVel")
        self.charset_size = self.settings.get_int("charset")

        module_addresses = self.settings.get_int_vector(
            "moduleAddresses", self.num_modules, fill=0x20
        )
        module_offsets = self.settings.get_int_vector("moduleOffsets", self.num_modules, fill=0)

        sda_pin = self.settings.get_int("sdaPin")
        scl_pin = self.settings.get_int("sclPin")
        self.i2c = I2C(0, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=200000)

        self.modules = []
        for i in range(self.num_modules):
            mod = SplitFlapModule(
                i2c=self.i2c, 
                address=module_addresses[i], 
                steps_per_rot=self.steps_per_rot, 
                step_offset=module_offsets[i] + self.display_offset, 
                magnet_pos=self.magnet_position, 
                charset_size=self.charset_size,
                sda_pin=sda_pin,
                scl_pin=scl_pin
             )
            self.modules.append(mod)

        for mod in self.modules:
            mod.init()

    def test_all(self):
        test_chars = SplitFlapModule.STANDARD_CHARS
        target_positions = [0] * self.num_modules
        
        for char in test_chars:
            for j in range(self.num_modules):
                target_positions[j] = self.modules[j].get_char_position(char)
            self.move_to(target_positions)
            utime.sleep_ms(500)

    def test_random(self, speed=MAX_RPM):
        test_chars = SplitFlapModule.STANDARD_CHARS
        target_positions = [0] * self.num_modules
        
        print("Target: ", end="")
        for i in range(self.num_modules):
            rand_char = urandom.choice(test_chars)
            target_positions[i] = self.modules[i].get_char_position(rand_char)
            print(rand_char, end="")
        print(" ")
        self.move_to(target_positions, speed)

    def test_count(self):
        max_count = 10 ** self.num_modules
        target_positions = [0] * self.num_modules
        
        for i in range(max_count):
            for j in range(self.num_modules):
                target_integer = (i % (10 ** (j + 1))) // (10 ** j)
                target_char = str(target_integer)
                target_positions[self.num_modules - j - 1] = self.modules[j].get_char_position(target_char)
            
            self.move_to(target_positions)
            utime.sleep_ms(250)

    def home(self, speed=MAX_RPM):
        print("Homing")
        target_positions = [0] * self.num_modules
        for i in range(self.num_modules):
            target_positions[i] = (self.modules[i].position - 1 + self.steps_per_rot) % self.steps_per_rot
            
        self.start_motors()
        self.move_to(target_positions, speed, release_motors=False)
        
        home_char = ' '
        for i in range(self.num_modules):
            target_positions[i] = self.modules[i].get_char_position(home_char)
        self.move_to(target_positions, speed)

    def home_to_string(self, home_string, speed=MAX_RPM, centering=True):
        print("Homing")
        target_positions = [0] * self.num_modules
        for i in range(self.num_modules):
            target_positions[i] = (self.modules[i].position - 1 + self.steps_per_rot) % self.steps_per_rot
            
        self.start_motors()
        self.move_to(target_positions, speed, release_motors=False)
        self.write_string(home_string, speed, centering)

    def home_to_char(self, home_char, speed=MAX_RPM):
        print("Homing")
        target_positions = [0] * self.num_modules
        for i in range(self.num_modules):
            target_positions[i] = (self.modules[i].position - 1 + self.steps_per_rot) % self.steps_per_rot
            
        self.start_motors()
        self.move_to(target_positions, speed, release_motors=False)
        
        for i in range(self.num_modules):
            target_positions[i] = self.modules[i].get_char_position(home_char)
        self.move_to(target_positions, speed)

    def write_char(self, input_char, speed=MAX_RPM):
        target_positions = [0] * self.num_modules
        for i in range(self.num_modules):
            target_positions[i] = self.modules[i].get_char_position(input_char)
        self.move_to(target_positions, speed)

    def write_string(self, input_string, speed=MAX_RPM, centering=True):
        display_string = input_string[:self.num_modules]
        
        if centering:
            total_padding = self.num_modules - len(display_string)
            padding_left = total_padding // 2
            padding_right = total_padding - padding_left
            display_string = (" " * padding_left) + display_string + (" " * padding_right)
        else:
            display_string = display_string + (" " * (self.num_modules - len(display_string)))
            
        target_positions = [0] * self.num_modules
        for i in range(len(display_string)):
            if i < self.num_modules:
                target_positions[i] = self.modules[i].get_char_position(display_string[i])
                
        self.move_to(target_positions, speed)
        
        if self.mqtt:
            self.mqtt.publish_state(display_string)

    @micropython.native
    def move_to(self, target_positions, speed=MAX_RPM, release_motors: bool = True):
        if not target_positions:
            return
            
        speed = max(2, min(int(speed), int(self.max_vel)))
        
        # Use integer arithmetic instead of floats for speed
        time_per_step = 60000000 // (speed * int(self.steps_per_rot))
        
        # Cache global lookups locally to speed up inner loop
        ticks_us = utime.ticks_us
        ticks_diff = utime.ticks_diff
        sleep_ms = utime.sleep_ms
        num_modules = int(self.num_modules)
        modules = self.modules
        
        current_time = ticks_us()
        check_interval_us = 20000 # const 20ms
        start_stop_delay_ms = 200 # const 200ms
        
        reset_latches = [True] * num_modules
        needs_stepping = [False] * num_modules
        last_step_times = [current_time] * num_modules
        last_sensor_check_time = current_time
        
        steps_per_rot = int(self.steps_per_rot)
        
        # Pre-process target positions and identify which need stepping
        has_needs_stepping = False
        for i in range(num_modules):
            target_positions[i] = max(0, min(target_positions[i], steps_per_rot - 1))
            if modules[i].position != target_positions[i]:
                needs_stepping[i] = True
                has_needs_stepping = True
                
        self.start_motors()
        sleep_ms(start_stop_delay_ms)
        
        while has_needs_stepping:
            current_time = ticks_us()
            
            # Fast stepping loop
            for i in range(num_modules):
                if needs_stepping[i]:
                    if ticks_diff(current_time, last_step_times[i]) > time_per_step:
                        mod = modules[i]
                        mod.step()
                        last_step_times[i] = ticks_us()
                        if mod.position == target_positions[i]:
                            needs_stepping[i] = False
                        
            # Fast sensor checking loop
            if ticks_diff(current_time, last_sensor_check_time) > check_interval_us:
                for i in range(num_modules):
                    if needs_stepping[i]:
                        mod = modules[i]
                        if mod.read_hall_effect_sensor():
                            if not reset_latches[i]:
                                mod.magnet_detected()
                                reset_latches[i] = True
                        elif reset_latches[i]:
                            reset_latches[i] = False
                            
                # Re-check if any module still needs stepping
                has_needs_stepping = False
                for i in range(num_modules):
                    if needs_stepping[i]:
                        has_needs_stepping = True
                        break
                        
                last_sensor_check_time = ticks_us()
                
        if release_motors:
            sleep_ms(start_stop_delay_ms)
            self.stop_motors()

    @micropython.native
    def start_motors(self):
        for mod in self.modules:
            mod.start()

    @micropython.native
    def stop_motors(self):
        for mod in self.modules:
            mod.stop()

    def set_mqtt(self, mqtt):
        self.mqtt = mqtt
