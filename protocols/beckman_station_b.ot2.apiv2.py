import math
from opentrons.types import Point
from opentrons import protocol_api
import os
import json

# metadata
metadata = {
    'protocolName': 'S5 Station B Version 1',
    'author': 'Nick <protocols@opentrons.com>',
    'source': 'Custom Protocol Request',
    'apiLevel': '2.0'
}

"""
REAGENT SETUP:

- slot 2 12-channel reservoir:
    - viral DNA/RNA buffer: channels 1-3
    - magbeads: channel 4
    - wash 1: channels 5-8
    - wash 2: channels 9-12

- slot 5 12-channel reservoir:
    - EtOH: channels 1-8
    - water: channel 12

"""

NUM_SAMPLES = 30
TIP_TRACK = False


def run(ctx: protocol_api.ProtocolContext):

    # load labware and modules
    tempdeck = ctx.load_module('tempdeck', '1')
    elution_plate = tempdeck.load_labware(
        'opentrons_96_aluminumblock_nest_wellplate_100ul',
        'cooled elution plate')
    reagent_res1 = ctx.load_labware(
        'nest_12_reservoir_15ml', '2', 'reagent reservoir 1')
    magdeck = ctx.load_module('magdeck', '4')
    magplate = magdeck.load_labware('usascientific_96_wellplate_2.4ml_deep')
    etoh = ctx.load_labware(
        'nest_1_reservoir_195ml', '5', 'reservoir for EtOH').wells()[0]
    waste = ctx.load_labware(
        'nest_1_reservoir_195ml', '7', 'waste reservoir').wells()[0].top()
    tips300 = [
        ctx.load_labware(
            'opentrons_96_filtertiprack_200ul', slot, '300µl tiprack')
        for slot in ['3', '6', '8', '9', '10', '11']
    ]

    # reagents and samples
    num_cols = math.ceil(NUM_SAMPLES/8)
    # mag_samples_m = [
    #     well for well in
    #     magplate.rows()[0][0::2] + magplate.rows()[0][1::2]][:num_cols]
    # elution_samples_m = [
    #     well for well in
    #     elution_plate.rows()[0][0::2] + magplate.rows()[0][1::2]][:num_cols]
    mag_samples_m = magplate.rows()[0][:num_cols]
    elution_samples_m = elution_plate.rows()[0][:num_cols]

    beads = reagent_res1.wells()[:2]
    wash = reagent_res1.wells()[3:9]
    water = reagent_res1.wells()[-1]

    # pipettes
    m300 = ctx.load_instrument('p300_multi_gen2', 'left', tip_racks=tips300)
    m300.flow_rate.aspirate = 150
    m300.flow_rate.dispense = 300

    tip_log = {'count': {}}
    folder_path = 'B'
    tip_file_path = folder_path + '/tip_log.json'
    if TIP_TRACK and not ctx.is_simulating():
        if os.path.isfile(tip_file_path):
            with open(tip_file_path) as json_file:
                data = json.load(json_file)
                if 'tips300' in data:
                    tip_log['count'][m300] = data['tips300']
                else:
                    tip_log['count'][m300] = 0
        else:
            tip_log['count'] = {m300: 0}
    else:
        tip_log['count'] = {m300: 0}

    tip_log['tips'] = {
        m300: [tip for rack in tips300 for tip in rack.rows()[0]]}
    tip_log['max'] = {
        m300: len(tip_log['tips'][m300])}

    def pick_up(pip):
        nonlocal tip_log
        if tip_log['count'][pip] == tip_log['max'][pip]:
            ctx.pause('Replace ' + str(pip.max_volume) + 'µl tipracks before \
resuming.')
            pip.reset_tipracks()
            tip_log['count'][pip] = 0
        pip.pick_up_tip(tip_log['tips'][pip][tip_log['count'][pip]])
        tip_log['count'][pip] += 1

    def remove_supernatant(vol):
        m300.flow_rate.aspirate = 30
        num_trans = math.ceil(vol/200)
        vol_per_trans = vol/num_trans
        for i, m in enumerate(mag_samples_m):
            side = -1 if i % 2 == 0 else 1
            loc = m.bottom(0.5).move(Point(x=side*2))
            if not m300.hw_pipette['has_tip']:
                pick_up(m300)
            for _ in range(num_trans):
                m300.move_to(m.center())
                m300.transfer(vol_per_trans, loc, waste, new_tip='never',
                              air_gap=30)
                m300.blow_out(waste)
            m300.drop_tip()
        m300.flow_rate.aspirate = 150

    # premix, transfer, and mix magnetic beads with sample
    for i, m in enumerate(mag_samples_m):
        pick_up(m300)
        if i == 0 or i == 8:
            for _ in range(20):
                m300.aspirate(200, beads[i//8].bottom(3))
                m300.dispense(200, beads[i//8].bottom(20))
        m300.transfer(205, beads[i//8], m, new_tip='never')
        m300.mix(10, 200, m)
        m300.blow_out(m.top(-2))
        m300.aspirate(20, m.top(-2))
        m300.drop_tip()

    # incubate off and on magnet
    ctx.delay(minutes=5, msg='Incubating on magnet for 5 minutes.')
    magdeck.engage()
    ctx.delay(minutes=10, msg='Incubating on magnet for 10 minutes.')

    # remove supernatant
    remove_supernatant(575)

    magdeck.disengage()

    # 2x washes
    for wash_ind in range(2):
        # transfer and mix wash
        for i, m in enumerate(mag_samples_m):
            wash_loc = i + wash_ind*len(mag_samples_m)
            pick_up(m300)
            side = 1 if i % 2 == 0 else -1
            loc = m.bottom(0.5).move(Point(x=side*2))
            m300.transfer(400, wash[wash_loc//4], m.top(), new_tip='never')
            m300.mix(10, 200, loc)
            m300.blow_out(m.top(-2))
            m300.aspirate(20, m.top(-2))
            m300.drop_tip()

        # incubate on magnet
        magdeck.engage()
        ctx.delay(minutes=5, msg='Incubating on magnet for 5 minutes.')

        # remove supernatant
        remove_supernatant(410)

        # transfer and mix wash
        pick_up(m300)
        m300.transfer(
            400, etoh, [m.top(3) for m in mag_samples_m], new_tip='never')
        ctx.delay(minutes=2, msg='Incubating in EtOH for 2 minutes.')

        # remove supernatant
        remove_supernatant(410)

        ctx.delay(minutes=1, msg='Airdrying for 1 minute.')

        magdeck.disengage()

    # transfer and mix water
    for m in mag_samples_m:
        pick_up(m300)
        side = 1 if i % 2 == 0 else -1
        loc = m.bottom(0.5).move(Point(x=side*2))
        m300.transfer(40, water, m.top(), new_tip='never')
        m300.mix(10, 30, loc)
        m300.blow_out(m.top(-2))
        m300.drop_tip()

    # incubate off and on magnet
    ctx.delay(minutes=2, msg='Incubating off magnet for 2 minutes.')
    magdeck.engage()
    ctx.delay(minutes=5, msg='Incubating on magnet for 5 minutes.')

    # transfer elution to clean plate
    m300.flow_rate.aspirate = 30
    for s, d in zip(mag_samples_m, elution_samples_m):
        pick_up(m300)
        side = -1 if i % 2 == 0 else 1
        loc = s.bottom(0.5).move(Point(x=side*2))
        m300.transfer(40, loc, d, new_tip='never')
        m300.blow_out(d.top(-2))
        m300.drop_tip()
    m300.flow_rate.aspirate = 150

    # track final used tip
    if not ctx.is_simulating():
        if not os.path.isdir(folder_path):
            os.mkdir(folder_path)
        data = {'tips300': tip_log['count'][m300]}
        with open(tip_file_path, 'w') as outfile:
            json.dump(data, outfile)