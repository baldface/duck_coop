import alarm
import board
import json
import time

from adafruit_ds3231 import DS3231
from adafruit_motorkit import MotorKit
from digitalio import DigitalInOut, Direction, Pull
from supervisor import runtime

try:
    from typing import Union, Literal
except ImportError:
    pass

# GLOBAL VARIABLES
# Implementation dependant things to tweak
TESTING = True
DOOR_OPEN_THROTTLE = -1.0  # swinging door open throttle
DOOR_CLOSE_THROTTLE = 1.0  # swinging door close throttle
DOOR_OPEN_75_THROTTLE = -0.75  # swinging door open throttle
DOOR_CLOSE_75_THROTTLE = 0.75  # swinging door close throttle
DOOR_MIN_TRANSITION_TIME_S = 7.5  # swinging door open/close @ 100% duty cycle duration in seconds
DOOR_75_TRANSITION_TIME_S = 11.5  # swinging door open/close @ 75% duty cycle duration in seconds
LOCK_OPEN_THROTTLE = -1.0  # door lock open throttle
LOCK_CLOSE_THROTTLE = 1.0  # door lock close throttle
LOCK_OPEN_75_THROTTLE = -0.75  # door lock open throttle
LOCK_CLOSE_75_THROTTLE = 0.75  # door lock close throttle
LOCK_MIN_TRANSITION_TIME_S = 1.2  # door lock open/close @ 100% duty cycle duration in seconds
LOCK_75_TRANSITION_TIME_S = 2.4  # door lock open/close @ 75% duty cycle duration in seconds
MANUAL_SWITCH_OPEN = True  # manual switch pin state corresponding to door open
MANUAL_SWITCH_CLOSE = False  # manual switch pin state corresponding to door close
#                 Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec
DAYS_PER_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

# Pins
MOTOR_DRV_PWR_EN_PIN = board.A0
MANUAL_SWITCH_STATE_PIN = board.A1
WAKE_PIN = board.A2
USB_PWR_STATE_PIN = board.D24
LED_PIN = board.LED

# SETUP PINS
# motor driver boost regulator enable pin
mtr_drv_pwr = DigitalInOut(MOTOR_DRV_PWR_EN_PIN)
mtr_drv_pwr.direction = Direction.OUTPUT
mtr_drv_pwr.value = False
# manual switch state pin
man_sw_state = DigitalInOut(MANUAL_SWITCH_STATE_PIN)
man_sw_state.direction = Direction.INPUT
man_sw_state.pull = Pull.DOWN
# led
led = DigitalInOut(LED_PIN)
led.direction = Direction.OUTPUT
led.value = False


# HELPER FUNCTIONS
def log(string: str):
    if TESTING:
        print(string)


def load_schedule():
    with open("//schedule.json", "r") as sch_obj:
        schedule = json.load(sch_obj)

    return schedule


def alarm_builder(dt: time.struct_time,
                  schedule: dict,
                  open_close: Literal["open", "close"],
                  today_tomorrow: Literal["today", "tomorrow"]):
    day_of_year = sum(DAYS_PER_MONTH[:dt.tm_mon - 1]) + dt.tm_mday  # determine what day of the year it is
    seconds = 0

    # get leap year
    if dt.tm_year % 4:
        leap_year = False
    else:
        leap_year = True

    # add leap year to day of year if it is a leap year and March or later
    if dt.tm_mon >= 3 and leap_year:
        day_of_year = day_of_year + 1

    # get year, month, week, day for today
    if today_tomorrow == "today":
        year = dt.tm_year
        month = dt.tm_mon
        day = dt.tm_mday
        weekday = dt.tm_wday
        if day_of_year % 7:
            week = int(day_of_year / 7) + 1
        else:
            week = int(day_of_year / 7)

    # get year, month, week, day for tomorrow
    else:
        # check if it is the last day of the year
        if dt.tm_mon == 12 and dt.tm_mday == 31:
            week = 1
            year = dt.tm_year + 1
            month = 1
            day = 1
        # check if it is the last day of the month
        elif (not leap_year and dt.tm_mday == DAYS_PER_MONTH[dt.tm_mon - 1]) or \
                (leap_year and dt.tm_mon == 2 and dt.tm_mday == DAYS_PER_MONTH[2 - 1] + 1):
            week = int(day_of_year / 7) + 1  # add 1 since int of fraction is 0
            year = dt.tm_year
            month = dt.tm_mon + 1
            day = 1
        else:
            week = int(day_of_year / 7) + 1  # add 1 since int of fraction is 0
            year = dt.tm_year
            month = dt.tm_mon
            day = dt.tm_mday + 1

        weekday = dt.tm_wday + 1
        if weekday == 7:
            weekday = 0

    hour = schedule[str(week)][open_close]["h"]
    minute = schedule[str(week)][open_close]["m"]

    return time.struct_time((year, month, day, hour, minute, seconds, weekday, -1, -1))


# VARIABLE TYPE DEFINITION
class DoorPartState(object):
    """"""

    def __init__(self, ram_idx: int):
        self.ram_idx = ram_idx
        self.is_closed = True
        self.is_open = False
        self.is_closing = False
        self.is_opening = False
        self.is_paused_closing = False
        self.is_paused_opening = False
        if alarm.sleep_memory[self.ram_idx] == 1:
            self.set_open()
        elif alarm.sleep_memory[self.ram_idx] == 2:
            self.set_closing()
        elif alarm.sleep_memory[self.ram_idx] == 3:
            self.set_opening()
        elif alarm.sleep_memory[self.ram_idx] == 4:
            self.set_paused_closing()
        elif alarm.sleep_memory[self.ram_idx] == 5:
            self.set_paused_opening()

    def set_closed(self):
        self._set_states(True, False, False, False, False, False)
        alarm.sleep_memory[self.ram_idx] = 0

    def set_open(self):
        self._set_states(False, True, False, False, False, False)
        alarm.sleep_memory[self.ram_idx] = 1

    def set_closing(self):
        self._set_states(False, False, True, False, False, False)
        alarm.sleep_memory[self.ram_idx] = 2

    def set_opening(self):
        self._set_states(False, False, False, True, False, False)
        alarm.sleep_memory[self.ram_idx] = 3

    def set_paused_closing(self):
        self._set_states(False, False, False, False, True, False)
        alarm.sleep_memory[self.ram_idx] = 4

    def set_paused_opening(self):
        self._set_states(False, False, False, False, False, True)
        alarm.sleep_memory[self.ram_idx] = 5

    def _set_states(self, is_closed: bool, is_open: bool,
                    is_closing: bool, is_opening: bool,
                    is_paused_closing: bool, is_paused_opening: bool):
        self.is_closed = is_closed
        self.is_open = is_open
        self.is_closing = is_closing
        self.is_opening = is_opening
        self.is_paused_closing = is_paused_closing
        self.is_paused_opening = is_paused_opening


class DoorTransitioningState(object):
    """"""

    def __init__(self, ram_idx: int):
        self.ram_idx = ram_idx
        self.is_none = True
        self.is_open = False
        self.is_close = False
        if alarm.sleep_memory[self.ram_idx] == 1:
            self.set_open()
        elif alarm.sleep_memory[self.ram_idx] == 2:
            self.set_close()

    def set_none(self):
        self._set_states(True, False, False)
        alarm.sleep_memory[self.ram_idx] = 0

    def set_open(self):
        self._set_states(False, True, False)
        alarm.sleep_memory[self.ram_idx] = 1

    def set_close(self):
        self._set_states(False, False, True)
        alarm.sleep_memory[self.ram_idx] = 2

    def _set_states(self, is_none: bool, is_open: bool, is_close: bool):
        self.is_none = is_none
        self.is_open = is_open
        self.is_close = is_close


class RamState(object):
    """"""

    def __init__(self, ram_idx: int):
        self.ram_idx = ram_idx
        self.is_retained = alarm.sleep_memory[ram_idx]

    def set_retained(self):
        self.is_retained = True


class ElapsedTime(object):
    """"""

    def __init__(self, ram_idx_s: int, ram_idx_100th_s: int):
        self._ram_idx_s = ram_idx_s
        self._ram_idx_100th_s = ram_idx_100th_s

    @property
    def sec(self):
        return alarm.sleep_memory[self._ram_idx_s] + (alarm.sleep_memory[self._ram_idx_100th_s] / 100)

    @sec.setter
    def sec(self, elapsed_time_s):
        alarm.sleep_memory[self._ram_idx_s] = int(elapsed_time_s)
        alarm.sleep_memory[self._ram_idx_100th_s] = int((elapsed_time_s - int(elapsed_time_s)) * 100)


class DoorPart(object):
    """"""

    def __init__(self, state_ram_idx: int,
                 elapsed_time_ram_idx_s: int,
                 elapsed_time_ram_idx_100th_s: int,
                 motor: Union[MotorKit.motor1, MotorKit.motor2, MotorKit.motor3, MotorKit.motor4]):
        self.state = DoorPartState(ram_idx=state_ram_idx)
        self.elapsed_time = ElapsedTime(ram_idx_s=elapsed_time_ram_idx_s, ram_idx_100th_s=elapsed_time_ram_idx_100th_s)
        self.motor = motor


# STATE MACHINE DEFINITION
class StateMachine(object):
    """"""

    def __init__(self):
        # INITIALIZE COMMUNICATION PROTOCOL
        i2c = board.I2C()
        # spi = board.SPI()

        # INITIALIZE MODULES
        self.rtc = DS3231(i2c=i2c)
        motor = MotorKit(i2c=i2c)

        # INITIALIZE VARIABLES
        self.state = None
        self.states = {}

        self.switch_state = man_sw_state.value  # get pin value at initialization
        self.lock = DoorPart(state_ram_idx=0,
                             elapsed_time_ram_idx_s=2,
                             elapsed_time_ram_idx_100th_s=3,
                             motor=motor.motor2)
        self.door = DoorPart(state_ram_idx=1,
                             elapsed_time_ram_idx_s=4,
                             elapsed_time_ram_idx_100th_s=5,
                             motor=motor.motor1)
        self.door_transition_state = DoorTransitioningState(ram_idx=6)
        self.ram_state = RamState(ram_idx=7)

        self.go_to_sleep_time = 0
        self.sleep_duration_s = 0
        self.elapsed_time = 0

    def add_state(self, state):
        self.states[state.name] = state

    def go_to_state(self, state_name):
        if self.state:
            log("Exiting {}".format(self.state.name))
            self.state.exit(self)
        self.state = self.states[state_name]
        log("Entering {}".format(self.state.name))
        self.state.enter(self)

    def execute(self):
        if self.state:
            log("executing {}".format(self.state.name))
            self.state.execute(self)


# STATES
# Abstract parent state class.
class State(object):

    def __init__(self):
        pass

    @property
    def name(self):
        return ''

    def enter(self, machine):
        pass

    def exit(self, machine):
        pass

    def execute(self, machine):
        pass


class Initialize(State):
    """"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "initialize"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        # get today's date, weekday, current time from user
        date = input("Enter date using the MM/DD/YYYY format: ").split("/")
        year = int(date[2])
        month = int(date[0])
        day = int(date[1])
        weekday = input("Enter weekday 0 - 6 (Monday - Sunday): ")
        weekday = int(weekday)
        current_time = input("Enter time using the HH:MM:SS format: ").split(":")
        hour = int(current_time[0])
        minute = int(current_time[1])
        second = int(current_time[2])

        # initialize rtc
        dt = time.struct_time((year, month, day, hour, minute, second, weekday, -1, -1))
        machine.rtc.datetime = dt

        schedule = load_schedule()

        today_alarm1 = alarm_builder(dt, schedule, "open", "today")
        today_alarm2 = alarm_builder(dt, schedule, "close", "today")
        tomorrow_alarm1 = alarm_builder(dt, schedule, "open", "tomorrow")
        tomorrow_alarm2 = alarm_builder(dt, schedule, "close", "tomorrow")

        if dt < today_alarm1:
            machine.rtc.alarm1 = (today_alarm1, "daily")
        else:
            machine.rtc.alarm1 = (tomorrow_alarm1, "daily")

        if dt < today_alarm2:
            machine.rtc.alarm2 = (today_alarm2, "daily")
        else:
            machine.rtc.alarm2 = (tomorrow_alarm2, "daily")

        machine.rtc.alarm1_interrupt = True
        machine.rtc.alarm2_interrupt = True

        # get door state from user
        door_lock_state = int(input("Enter Door and Lock state (0=Closed, 1=Open): "))

        # set door state
        if door_lock_state == 0:
            machine.door.state.set_closed()
            machine.lock.state.set_closed()
        else:
            machine.door.state.set_open()
            machine.lock.state.set_open()

        machine.ram_state.set_retained()

        # wait for user to unplug usb
        print("Please remove USB...")
        while runtime.serial_connected:
            time.sleep(0.5)

        machine.go_to_state("waiting")


class Waiting(State):
    """Wait for something to initiate wakeup"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "waiting"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        led.value = False
        pin_alarm = alarm.pin.PinAlarm(pin=WAKE_PIN, value=False, edge=True, pull=False)
        if not machine.door_transition_state.is_none:  # do a light sleep if we are in the middle of opening or closing
            print("doing light sleep")
            print("sleep time: {}".format(machine.sleep_duration_s))
            machine.go_to_sleep_time = time.monotonic()
            print("monotonic time before sleep: {}".format(machine.go_to_sleep_time))
            time_alarm = alarm.time.TimeAlarm(monotonic_time=(time.monotonic() + machine.sleep_duration_s))
            alarm.light_sleep_until_alarms(time_alarm, pin_alarm)
        else:
            print("doing deep sleep")
            alarm.exit_and_deep_sleep_until_alarms(pin_alarm)

        # if we got here, it was a light sleep
        led.value = True
        machine.go_to_state("get_reason_for_wake_up")


class GetReasonForWakeUp(State):
    """Determine reason for wake up"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "get_reason_for_wake_up"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        time.sleep(0.05)  # let switch value settle
        machine.switch_state = man_sw_state.value
        print("switch value: {}".format(machine.switch_state))

        if machine.rtc.alarm1_status or machine.rtc.alarm2_status:
            machine.go_to_state("service_rtc")
        elif isinstance(alarm.wake_alarm, alarm.time.TimeAlarm):
            print("woke up due to time alarm")
            machine.go_to_state("wake_up")
        elif machine.switch_state == MANUAL_SWITCH_OPEN:
            print("woke up due to switch it is: True")
            machine.door_transition_state.set_open()
            machine.go_to_state("wake_up")
        else:
            print("woke up due to switch it is: False")
            machine.door_transition_state.set_close()
            machine.go_to_state("wake_up")


class WakeUp(State):
    """Determine what to do after wake up"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "wake_up"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        if machine.door_transition_state.is_open and machine.lock.state.is_open and machine.door.state.is_open:
            machine.door_transition_state.set_none()
            machine.go_to_state("waiting")
        elif machine.door_transition_state.is_close and machine.lock.state.is_closed and machine.door.state.is_closed:
            machine.door_transition_state.set_none()
            machine.go_to_state("waiting")
        elif (machine.door_transition_state.is_open and machine.lock.state.is_closed) or \
                machine.lock.state.is_opening or \
                machine.lock.state.is_closing or \
                machine.lock.state.is_paused_opening or \
                machine.lock.state.is_paused_closing:
            machine.go_to_state("service_lock")
        elif (machine.door_transition_state.is_close and machine.door.state.is_open) or \
                machine.door.state.is_opening or \
                machine.door.state.is_closing or \
                machine.door.state.is_paused_opening or \
                machine.door.state.is_paused_closing:
            machine.go_to_state("service_door")
        else:
            machine.go_to_state("error")


class ServiceRtc(State):

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "service_rtc"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        dt = machine.rtc.datetime  # get current dt
        schedule = load_schedule()  # load schedule

        if machine.rtc.alarm1_status:
            # morning alarm went off
            machine.rtc.alarm1_status = False
            machine.rtc.alarm1 = (alarm_builder(dt, schedule, "open", "tomorrow"), "daily")
            machine.door_transition_state.set_open()

        else:
            # night alarm went off
            machine.rtc.alarm2_status = False
            machine.rtc.alarm2 = (alarm_builder(dt, schedule, "close", "tomorrow"), "daily")
            machine.door_transition_state.set_close()

        machine.go_to_state("wake_up")


class ServiceLock(State):
    """"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "service_lock"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        mtr_drv_pwr.value = True
        elapsed_time_s = machine.lock.elapsed_time.sec + time.monotonic() - machine.go_to_sleep_time
        print("time after sleep: {}".format(time.monotonic()))
        print("elapsed time: {}".format(elapsed_time_s))

        if machine.door_transition_state.is_open:
            if machine.lock.state.is_closed:
                print("lock 0")
                machine.lock.elapsed_time.sec = 0.0
                machine.lock.state.set_opening()
                machine.sleep_duration_s = LOCK_MIN_TRANSITION_TIME_S
                machine.lock.motor.throttle = LOCK_OPEN_THROTTLE
                machine.go_to_state("waiting")
            elif machine.lock.state.is_opening and elapsed_time_s < LOCK_MIN_TRANSITION_TIME_S:
                print("lock 1")
                machine.lock.elapsed_time.sec = elapsed_time_s
                machine.sleep_duration_s = LOCK_MIN_TRANSITION_TIME_S - elapsed_time_s
                machine.go_to_state("waiting")
            elif machine.lock.state.is_opening and elapsed_time_s > LOCK_MIN_TRANSITION_TIME_S:
                print("lock 2")
                machine.lock.elapsed_time.sec = 0.0
                machine.lock.state.set_open()
                machine.lock.motor.throttle = None
                machine.go_to_state("service_door")
            elif machine.lock.state.is_closing:
                print("lock 3")
                machine.lock.elapsed_time.sec = elapsed_time_s
                mtr_drv_pwr.value = False
                machine.lock.state.set_paused_closing()
                machine.door_transition_state.set_none()
                machine.lock.motor.throttle = None
                machine.go_to_state("waiting")
            elif machine.lock.state.is_paused_closing:
                print("lock 4")
                machine.sleep_duration_s = machine.lock.elapsed_time.sec
                machine.lock.state.set_opening()
                machine.lock.motor.throttle = LOCK_OPEN_THROTTLE
                machine.go_to_state("waiting")
            elif machine.lock.state.is_paused_opening:
                print("lock 5")
                machine.sleep_duration_s = LOCK_MIN_TRANSITION_TIME_S - machine.lock.elapsed_time.sec
                machine.lock.state.set_opening()
                machine.lock.motor.throttle = LOCK_OPEN_THROTTLE
                machine.go_to_state("waiting")
        elif machine.door_transition_state.is_close:
            if machine.lock.state.is_open:
                print("lock 6")
                machine.lock.elapsed_time.sec = 0.0
                machine.lock.state.set_closing()
                machine.sleep_duration_s = LOCK_MIN_TRANSITION_TIME_S
                machine.lock.motor.throttle = LOCK_CLOSE_THROTTLE
                machine.go_to_state("waiting")
            elif machine.lock.state.is_closing and elapsed_time_s < LOCK_MIN_TRANSITION_TIME_S:
                print("lock 7")
                machine.lock.elapsed_time.sec = elapsed_time_s
                machine.sleep_duration_s = LOCK_MIN_TRANSITION_TIME_S - elapsed_time_s
                machine.go_to_state("waiting")
            elif machine.lock.state.is_closing and elapsed_time_s > LOCK_MIN_TRANSITION_TIME_S:
                print("lock 8")
                machine.lock.elapsed_time.sec = 0.0
                mtr_drv_pwr.value = False
                machine.lock.state.set_closed()
                machine.door_transition_state.set_none()
                machine.lock.motor.throttle = None
                machine.go_to_state("waiting")
            elif machine.lock.state.is_opening:
                print("lock 9")
                machine.lock.elapsed_time.sec = elapsed_time_s
                mtr_drv_pwr.value = False
                machine.lock.state.set_paused_opening()
                machine.door_transition_state.set_none()
                machine.lock.motor.throttle = None
                machine.go_to_state("waiting")
            elif machine.lock.state.is_paused_opening:
                print("lock 10")
                machine.sleep_duration_s = machine.lock.elapsed_time.sec
                machine.lock.state.set_closing()
                machine.lock.motor.throttle = LOCK_CLOSE_THROTTLE
                machine.go_to_state("waiting")
            elif machine.lock.state.is_paused_closing:
                print("lock 11")
                machine.sleep_duration_s = LOCK_MIN_TRANSITION_TIME_S - machine.lock.elapsed_time.sec
                machine.lock.state.set_closing()
                machine.lock.motor.throttle = LOCK_CLOSE_THROTTLE
                machine.go_to_state("waiting")


class ServiceDoor(State):
    """"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "service_door"

    def enter(self, machine):
        State.enter(self, machine)

    def exit(self, machine):
        State.exit(self, machine)

    def execute(self, machine: StateMachine):
        mtr_drv_pwr.value = True
        elapsed_time_s = machine.door.elapsed_time.sec + time.monotonic() - machine.go_to_sleep_time
        print("time after sleep: {}".format(time.monotonic()))
        print("elapsed time: {}".format(elapsed_time_s))

        if machine.door_transition_state.is_open:
            if machine.door.state.is_closed:
                print("door 0")
                machine.door.elapsed_time.sec = 0.0
                machine.door.state.set_opening()
                machine.sleep_duration_s = DOOR_MIN_TRANSITION_TIME_S
                machine.door.motor.throttle = DOOR_OPEN_THROTTLE
                machine.go_to_state("waiting")
            elif machine.door.state.is_opening and elapsed_time_s < DOOR_MIN_TRANSITION_TIME_S:
                print("door 1")
                machine.door.elapsed_time.sec = elapsed_time_s
                machine.sleep_duration_s = DOOR_MIN_TRANSITION_TIME_S - elapsed_time_s
                machine.go_to_state("waiting")
            elif machine.door.state.is_opening and elapsed_time_s > DOOR_MIN_TRANSITION_TIME_S:
                print("door 2")
                machine.door.elapsed_time.sec = 0.0
                machine.door.state.set_open()
                machine.door_transition_state.set_none()
                machine.door.motor.throttle = None
                machine.go_to_state("waiting")
            elif machine.door.state.is_closing:
                print("door 3")
                mtr_drv_pwr.value = False
                machine.door.elapsed_time.sec = elapsed_time_s
                machine.door.state.set_paused_closing()
                machine.door_transition_state.set_none()
                machine.door.motor.throttle = None
                machine.go_to_state("waiting")
            elif machine.door.state.is_paused_closing:
                print("door 4")
                machine.sleep_duration_s = machine.door.elapsed_time.sec
                machine.door.state.set_opening()
                machine.door.motor.throttle = DOOR_OPEN_THROTTLE
                machine.go_to_state("waiting")
            elif machine.door.state.is_paused_opening:
                print("door 5")
                machine.sleep_duration_s = DOOR_MIN_TRANSITION_TIME_S - machine.door.elapsed_time.sec
                machine.door.state.set_opening()
                machine.door.motor.throttle = DOOR_OPEN_THROTTLE
                machine.go_to_state("waiting")
        elif machine.door_transition_state.is_close:
            if machine.door.state.is_open:
                print("door 6")
                machine.door.elapsed_time.sec = 0.0
                machine.door.state.set_closing()
                machine.sleep_duration_s = DOOR_MIN_TRANSITION_TIME_S
                machine.door.motor.throttle = DOOR_CLOSE_THROTTLE
                machine.go_to_state("waiting")
            elif machine.door.state.is_closing and elapsed_time_s < DOOR_MIN_TRANSITION_TIME_S:
                print("door 7")
                machine.door.elapsed_time.sec = elapsed_time_s
                machine.sleep_duration_s = DOOR_MIN_TRANSITION_TIME_S - elapsed_time_s
                machine.go_to_state("waiting")
            elif machine.door.state.is_closing and elapsed_time_s > DOOR_MIN_TRANSITION_TIME_S:
                print("door 8")
                machine.door.elapsed_time.sec = 0.0
                machine.door.state.set_closed()
                machine.door.motor.throttle = None
                machine.go_to_state("service_lock")
            elif machine.door.state.is_opening:
                print("door 9")
                mtr_drv_pwr.value = False
                machine.door.elapsed_time.sec = elapsed_time_s
                machine.door.state.set_paused_opening()
                machine.door_transition_state.set_none()
                machine.door.motor.throttle = None
                machine.go_to_state("waiting")
            elif machine.door.state.is_paused_opening:
                print("door 10")
                machine.sleep_duration_s = machine.door.elapsed_time.sec
                machine.door.state.set_closing()
                machine.door.motor.throttle = DOOR_CLOSE_THROTTLE
                machine.go_to_state("waiting")
            elif machine.door.state.is_paused_closing:
                print("door 11")
                machine.sleep_duration_s = DOOR_MIN_TRANSITION_TIME_S - machine.door.elapsed_time.sec
                machine.door.state.set_closing()
                machine.door.motor.throttle = DOOR_CLOSE_THROTTLE
                machine.go_to_state("waiting")


class RecoverFromImproperReset(State):
    """"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "recover_from_improper_reset"

    def enter(self, machine):
        super().enter(machine)

    def exit(self, machine):
        super().exit(machine)

    def execute(self, machine: StateMachine):
        if machine.ram_state:
            print("ram retained, we good")
            # if ram still retained just resume whatever was happening before the improper reset
            if machine.door_transition_state.is_none:
                machine.go_to_state("waiting")
            else:
                machine.go_to_state("wake_up")
        else:
            print("ram fucked, set the door where it probably should be")
            # if ram was lost, figure out what state the door should be in based on alarms and attempt to move it to
            # that position
            machine.ram_state.set_retained()
            machine.rtc.alarm1_interrupt = True  # not sure this is needed
            machine.rtc.alarm2_interrupt = True  # not sure this is needed
            dt = machine.rtc.datetime
            schedule = load_schedule()

            today_alarm1 = alarm_builder(dt, schedule, "open", "today")
            today_alarm2 = alarm_builder(dt, schedule, "close", "today")

            if dt < today_alarm1 or dt > today_alarm2:
                machine.door_transition_state.set_close()
                machine.door.state.set_open()
                machine.lock.state.set_open()
                machine.go_to_state("wake_up")
            else:
                machine.door_transition_state.set_open()
                machine.door.state.set_closed()
                machine.lock.state.set_closed()
                machine.go_to_state("wake_up")


class Error(State):
    """"""

    def __init__(self):
        super().__init__()

    @property
    def name(self):
        return "error"

    def enter(self, machine):
        super().enter(machine)

    def exit(self, machine):
        super().exit(machine)

    def execute(self, machine):
        led.value = True
        time.sleep(1)
        led.value = False
        time.sleep(1)


# MAIN
duck_coop = StateMachine()
duck_coop.add_state(Initialize())
duck_coop.add_state(Waiting())
duck_coop.add_state(WakeUp())
duck_coop.add_state(GetReasonForWakeUp())
duck_coop.add_state(ServiceRtc())
duck_coop.add_state(ServiceLock())
duck_coop.add_state(ServiceDoor())
duck_coop.add_state(RecoverFromImproperReset())
duck_coop.add_state(Error())

if alarm.wake_alarm is None:  # no alarm cause restart of code
    if duck_coop.rtc.datetime.tm_year == 2000:
        duck_coop.go_to_state("initialize")
        duck_coop.execute()
    else:  # unintentional restart happened
        duck_coop.go_to_state("recover_from_improper_reset")
        duck_coop.execute()
else:  # pin alarm caused restart of code
    led.value = True
    duck_coop.go_to_state("get_reason_for_wake_up")

while True:
    duck_coop.execute()
