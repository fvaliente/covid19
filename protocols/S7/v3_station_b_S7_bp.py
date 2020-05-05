from opentrons import types
import json
import os
import math

metadata = {
    'protocolName': 'Version 3 S7 Station B (BP Genomics RNA Extraction)',
    'author': 'Nick <ndiehl@opentrons.com',
    'apiLevel': '2.3'
}

NUM_SAMPLES = 8  # start with 8 samples, slowly increase to 48, then 94 (max is 94)
TIP_TRACK = False


def run(ctx):

    # load labware and pipettes
    tips300 = [ctx.load_labware('opentrons_96_tiprack_300ul', slot, '200µl filtertiprack')
               for slot in ['3', '6', '8', '9', '10']]
    parkingrack = ctx.load_labware(
        'opentrons_96_tiprack_300ul', '7', 'empty tiprack for parking')

    m300 = ctx.load_instrument(
        'p300_multi_gen2', 'left', tip_racks=tips300)

    magdeck = ctx.load_module('magdeck', '4')
    magheight = 13.7
    magplate = magdeck.load_labware('nest_96_deepwell_2ml')
    # magplate = magdeck.load_labware('biorad_96_wellplate_200ul_pcr')
    tempdeck = ctx.load_module('Temperature Module Gen2', '1')
    flatplate = tempdeck.load_labware(
                'opentrons_96_aluminumblock_nest_wellplate_100ul',)
    liqwaste2 = ctx.load_labware(
                'nest_1_reservoir_195ml', '11', 'Liquid Waste')
    waste2 = liqwaste2['A1'].top()
    etoh = ctx.load_labware(
        'nest_1_reservoir_195ml', '2', 'Trough with Ethanol').wells()[0]
    trough2 = ctx.load_labware(
                    'nest_12_reservoir_15ml', '5', 'Trough with Reagents')
    bind1 = trough2.wells()[:2]
    wb1 = trough2.wells()[3:5]
    water = trough2.wells()[-1]

    num_cols = math.ceil(NUM_SAMPLES/8)
    mag_samples_m = magplate.rows()[0][:NUM_SAMPLES]
    elution_samples_m = flatplate.rows()[0][:num_cols]
    parking_spots = parkingrack.rows()[0][:num_cols]

    magdeck.disengage()  # just in case
    tempdeck.set_temperature(4)

    m300.flow_rate.aspirate = 50
    m300.flow_rate.dispense = 150
    m300.flow_rate.blow_out = 300

    folder_path = '/data/B'
    tip_file_path = folder_path + '/tip_log.json'
    tip_log = {'count': {}}
    if TIP_TRACK and not ctx.is_simulating():
        if os.path.isfile(tip_file_path):
            with open(tip_file_path) as json_file:
                data = json.load(json_file)
                if 'tips300' in data:
                    tip_log['count'][m300] = data['tips300']
                else:
                    tip_log['count'][m300] = 0
        else:
            tip_log['count'][m300] = 0
    else:
        tip_log['count'] = {m300: 0}

    tip_log['tips'] = {
        m300: [tip for rack in tips300 for tip in rack.rows()[0]]}
    tip_log['max'] = {m300: len(tip_log['tips'][m300])}

    def pick_up(pip, loc=None):
        nonlocal tip_log
        if tip_log['count'][pip] == tip_log['max'][pip]:
            ctx.pause('Replace ' + str(pip.max_volume) + 'µl tipracks before \
resuming.')
            pip.reset_tipracks()
            tip_log['count'][pip] = 0
        if loc:
            pip.pick_up_tip(loc)
        else:
            pip.pick_up_tip(tip_log['tips'][pip][tip_log['count'][pip]])
            tip_log['count'][pip] += 1

    switch = True
    drop_count = 0
    drop_threshold = 192  # number of tips trash will accommodate before prompting user to empty

    def drop(pip):
        nonlocal switch
        nonlocal drop_count
        side = 1 if switch else -1
        drop_loc = ctx.loaded_labwares[12].wells()[0].top().move(
            types.Point(x=40*side))
        pip.drop_tip(drop_loc)
        switch = not switch
        drop_count += 1
        if drop_count == drop_threshold:
            ctx.pause('Please empty tips from waste before resuming.')
            drop_count = 0

    def well_mix(reps, loc, vol):
        loc1 = loc.bottom().move(types.Point(x=1, y=0, z=0.5))
        loc2 = loc.bottom().move(types.Point(x=1, y=0, z=3.5))
        m300.aspirate(20, loc1)
        for _ in range(reps-1):
            m300.aspirate(vol, loc1)
            m300.dispense(vol, loc2)
        m300.dispense(20, loc2)

    pick_up(m300)
    for i, well in enumerate(mag_samples_m):
        source = bind1[i//8]
        if i % 8 == 0:  # mix beads if accessing new column
            for _ in range(5):
                m300.aspirate(180, source.bottom(0.5))
                m300.dispense(180, source.bottom(5))
        m300.transfer(200, source, well.top(-3), new_tip='never')
        m300.blow_out(well.top())

    for well, spot in zip(mag_samples_m, parking_spots):
        if not m300.hw_pipette['has_tip']:
            pick_up(m300)
        well_mix(8, well, 140)
        m300.blow_out(well.top())
        m300.air_gap(20)
        m300.drop_tip(spot)

    ctx.comment('Incubating at room temp for 5 minutes. With mixing.')
    for mix in range(2):
        for well, tip in zip(mag_samples_m, parking_spots):
            pick_up(m300, tip)
            well_mix(15, well, 120)
            m300.blow_out(well.top(-10))
            m300.air_gap(20)
        if mix == 0:
            m300.drop_tip(tip)
        else:
            drop(m300)

    # Step 4 - engage magdeck for 7 minutes
    magdeck.engage(height=magheight)
    ctx.delay(minutes=7, msg='Incubating on MagDeck for 7 minutes.')

    # Step 5 - Remove supernatant
    def supernatant_removal(vol, src, dest, side):
        m300.flow_rate.aspirate = 25
        num_trans = math.ceil(vol/200)
        vol_per_trans = vol/num_trans
        for _ in range(num_trans):
            m300.transfer(
                vol_per_trans, src.bottom().move(types.Point(x=side, y=0, z=0.5)),
                dest, air_gap=20, new_tip='never')
            m300.blow_out(dest)
        m300.flow_rate.aspirate = 50

    for i, well in enumerate(mag_samples_m):
        pick_up(m300)
        side = -1 if i % 2 == 0 else 1
        supernatant_removal(1160, well, waste2, side)
        drop(m300)

    magdeck.disengage()

    def wash_step(src, mtimes, wasteman):
        pick_up(m300)
        if src == wb1:
            for i, well in enumerate(mag_samples_m):
                m300.transfer(200, src[i//8], well.top(-3), new_tip='never')
        else:
            for well in mag_samples_m:
                m300.transfer(200, src, well.top(-3), new_tip='never')

        for well, spot in zip(mag_samples_m, parking_spots):
            if not m300.hw_pipette['has_tip']:
                pick_up(m300)
            well_mix(mtimes, well, 180)
            m300.blow_out(well.top(-3))
            m300.air_gap(20)
            m300.drop_tip(spot)

        magdeck.engage(height=magheight)
        ctx.delay(minutes=6, msg='Incubating on MagDeck for 6 minutes.')

        for i, (well, tip) in enumerate(zip(mag_samples_m, parking_spots)):
            pick_up(m300, tip)
            side = -1 if i % 2 == 0 else 1
            supernatant_removal(200, well, wasteman, side)
            drop(m300)

        magdeck.disengage()

    wash_step(wb1, 20, waste2)

    wash_step(etoh, 15, waste2)

    def eth_wash(src, waste):
        pick_up(m300)
        m300.flow_rate.aspirate = 50
        m300.flow_rate.dispense = 30
        for well in mag_samples_m:
            m300.transfer(200, src,
                          well.top().move(types.Point(x=-1, y=0, z=-3)),
                          new_tip='never')
            m300.blow_out(well.top(-3))
        # m300.touch_tip()
        # m300.return_tip()

        # pick_up(m300, tips200[tips])
        m300.flow_rate.aspirate = 30
        m300.flow_rate.dispense = 150
        for i, well in enumerate(mag_samples_m):
            if not m300.hw_pipette['has_tip']:
                pick_up(m300)
            side = -1 if i % 2 == 0 else 1
            m300.transfer(
                200, well.bottom().move(types.Point(x=side, y=0, z=0.5)),
                waste, air_gap=20, new_tip='never')
            drop(m300)

    magdeck.engage(height=magheight)
    eth_wash(etoh, waste2)

    eth_wash(etoh, waste2)

    # ctx.comment('Allowing beads to air dry for 2 minutes.')
    # ctx.delay(minutes=2)
    #
    # for well, tip in zip(mag_samples_m, tips6):
    #     pick_up(m300, tip)
    #     m300.transfer(
    #         200, well.bottom().move(types.Point(x=-0.4, y=0, z=0.3)),
    #         waste2, new_tip='never')
    #     drop(m300)
    m300.flow_rate.aspirate = 50

    ctx.delay(minutes=10, msg='Allowing beads to air dry for 10 minutes.')

    magdeck.disengage()

    pick_up(m300)
    for well in mag_samples_m:
        m300.aspirate(30, water.top())
        m300.aspirate(30, water)
        m300.dispense(60, well.top(-5))
        m300.blow_out(well.top(-3))

    for well in mag_samples_m:
        if not m300.hw_pipette['has_tip']:
            pick_up(m300)
        for _ in range(12):
            m300.dispense(
                30, well.bottom().move(types.Point(x=1, y=0, z=2)))
            m300.aspirate(
                30, well.bottom().move(types.Point(x=1, y=0, z=0.5)))
        m300.dispense(30, well)
        m300.dispense(30, well.top(-4))
        m300.blow_out(well.top(-4))
        m300.air_gap(20)
        drop(m300)

    ctx.delay(minutes=2, msg='Incubating at room temp for 2 minutes.')

    # Step 21 - Transfer elution_samples_m to clean plate
    magdeck.engage(height=magheight)
    ctx.comment('Incubating on MagDeck for 5 minutes.')
    ctx.delay(minutes=5)

    m300.flow_rate.aspirate = 10
    for i, (src, dest) in enumerate(zip(mag_samples_m, elution_samples_m)):
        pick_up(m300)
        side = -1 if i % 2 == 0 else 1
        m300.aspirate(20, src.top())
        m300.aspirate(
            30, src.bottom().move(types.Point(x=0.8*side, y=0, z=0.6)))
        m300.air_gap(20)
        m300.dispense(70, dest)
        m300.blow_out(dest.top(-2))
        m300.air_gap(20)
        drop(m300)

    magdeck.disengage()