import utime
from micropython import const

# Pre-calculate constant step states for fast lookup
_STEP_STATES = (
    const(0b1111111111100111),        # Step 0
    const(0b1111111111110011),        # Step 1
    const(0b1111111111111001),        # Step 2
    const(0b1111111111101101)         # Step 3
)

# _INIT_STATE: idle/stop bit pattern for module outputs
_INIT_STATE = const(0b1111111111100001)
HALL_MASK = const(1 << 15)

_recover_i2c_attempted = False

def _reset_recovery_flag():
    """Reset the I2C recovery flag. Call once on every fresh boot or bus reinit."""
    global _recover_i2c_attempted
    _recover_i2c_attempted = False


def _attempt_bus_recovery(i2c, sda_pin, scl_pin, address):
    """Restore a dead I2C bus and return True if successful.

    Deinitializes the I2C peripheral, waits for the bus to settle,
    then reinitializes it with the given pin/frequency config.  A brief
    write probe tests whether any devices (including this module) are
    reachable after reset.
    """
    global _recover_i2c_attempted
    # Only attempt recovery once per bus reboot cycle to avoid busy loops
    if _recover_i2c_attempted:
        return False
    _recover_i2c_attempted = True

    from machine import I2C as I2CClass, Pin

    try:
        i2c.deinit()
    except Exception:
        pass

    utime.sleep_ms(10)  # Let the bus settle after deinit

    try:
        new_i2c = I2CClass(0, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=200000)
    except Exception:
        return False

    utime.sleep_ms(5)  # Allow I2C addresses to enumerate

    # Probe write: attempt a dead-bus-safe operation
    buf_probe = bytearray(2)
    try:
        new_i2c.writeto(address, buf_probe)
    except OSError:
        pass  # Device may not respond; bus itself might still be valid

    i2c.deinit()
    for _ in range(3):  # Retry reassigning in case of race conditions
        try:
            new_i2c.writeto(address, buf_probe)
            break
        except OSError:
            utime.sleep_ms(5)
    else:
        new_i2c.deinit()
        return False

    return True


class SplitFlapModule:
    STANDARD_CHARS = (' ', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9')
    EXTENDED_CHARS = (' ', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', "'", ':', '?', '!', '.', '-', '/', '$', '@', '#', '%')

    def __init__(self, i2c, address, steps_per_rot, step_offset, magnet_pos, charset_size, sda_pin=0, scl_pin=0):
        self.i2c = i2c
        self.address = address
        self.position = 0
        self.step_number = 0
        self.steps_per_rot = steps_per_rot
        self.has_errored = False

        self.magnet_position = magnet_pos + step_offset

        # Store I2C pin config for bus recovery
        self._sda_pin = sda_pin
        self._scl_pin = scl_pin

        self.num_chars = 0
        if charset_size == 48:
            self.chars = self.EXTENDED_CHARS
            self.num_chars = 48
        else:
            self.chars = self.STANDARD_CHARS
            self.num_chars = 37

        self.char_positions = [
            round((i * self.steps_per_rot + self.num_chars / 2) / self.num_chars) for i in range(self.num_chars)
         ]

        # Build O(1) lookup from character -> step position
        self._char_to_pos = {c: p for c, p in zip(self.chars, self.char_positions)}

        # Pre-allocate buffer for I2C writes to avoid GC allocation during stepping
        self._buf = bytearray(2)

    @micropython.native
    def write_io(self, data: int):
        """Write step state to the module via I2C. Attempts automatic bus recovery on error."""
        # Cache locals
        buf = self._buf
        buf[0] = data & 0xFF
        buf[1] = (data >> 8) & 0xFF
        try:
            self.i2c.writeto(self.address, buf)
        except OSError as e:
            if not self.has_errored:
                self.has_errored = True
                print("Error writing data to module", self.address, "error code:", e)
                
                # Attempt automatic I2C bus recovery on first error
                recovered = _attempt_bus_recovery(self.i2c, self._sda_pin, self._scl_pin, self.address)
                
                if recovered:
                    print("I2C bus recovered for module", self.address)
                    # Retry the write with fresh bus
                    try:
                        self.i2c.writeto(self.address, buf)
                        self.has_errored = False  # Clear error — recovery succeeded
                        return
                    except OSError as e2:
                        print("Recovery write failed for module", self.address, "error code:", e2)
                else:
                    print("I2C bus recovery failed for module", self.address)
            # If we got here, the bus is still dead — module remains errored

    def init(self):
        """Initialize module: set idle, then pulse steps for homing."""
        _reset_recovery_flag()  # Fresh boot/initialization resets recovery state
        
        self.write_io(_INIT_STATE)

        self.stop()

        init_delay_ms = 100
        utime.sleep_ms(init_delay_ms)
        for _ in range(4):
            self.step()
            utime.sleep_ms(init_delay_ms)

        self.stop()

    def get_char_position(self, input_char):
        """Return step index for a character, or 0 if not found."""
        if not isinstance(input_char, str):
            return 0
        return self._char_to_pos.get(input_char.upper(), 0)

    @micropython.native
    def stop(self):
        """Stop the motor by sending the idle state."""
        self.write_io(_INIT_STATE)

    @micropython.native
    def start(self):
        """Advance step phase and begin rotation (no position update)."""
        self.step_number = (self.step_number + 3) % 4
        self.step(update_position=False)

    @micropython.native
    def step(self, update_position: bool = True):
        """Execute one step using precomputed step states."""
        # Look up state directly from pre-calculated tuple using step_number
        self.write_io(_STEP_STATES[self.step_number])

        if update_position:
            self.position = (self.position + 1) % self.steps_per_rot

        # Bitwise AND 3 is equivalent to modulo 4 but faster
        self.step_number = (self.step_number + 1) & 3

    @micropython.native
    def read_hall_effect_sensor(self) -> bool:
        """Read Hall-effect sensor. Returns False on I2C/read error."""
        if self.has_errored:
            # Attempt recovery before bailing out — bus may have recovered externally
            recovered = _attempt_bus_recovery(self.i2c, self._sda_pin, self._scl_pin, self.address)
            if recovered:
                self.has_errored = False
                print("Bus recovered for module", self.address, "during sensor read")
            else:
                return False

        try:
            data = self.i2c.readfrom(self.address, 2)
            if len(data) == 2:
                input_state = data[0] | (data[1] << 8)
                return (input_state & HALL_MASK) != 0
        except OSError:
            pass
        return False

    def reset(self):
        """Reset the module to initial idle state. Call after bus recovery."""
        self.has_errored = False
        self.write_io(_INIT_STATE)
