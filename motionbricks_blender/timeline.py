"""Place generated actions in Blender's NLA stack."""

import math


def add_to_nla(adapter, action):
    rig = adapter.rig
    had_animation = rig.animation_data is not None
    animation = rig.animation_data_create()
    if animation.use_tweak_mode:
        raise ValueError("请退出 NLA 编辑模式后再生成。")
    tracks = animation.nla_tracks
    created = []
    previous, previous_slot = animation.action, animation.action_slot

    def add_strip(source, slot, track_name):
        track = tracks.new(prev=tracks[-1]) if len(tracks) else tracks.new()
        created.append(track)
        track.name = track_name
        start = source.frame_range[0]
        strip = track.strips.new(source.name, math.floor(start), source)
        strip.action_slot = slot
        shift = start - strip.frame_start
        if shift:
            strip.frame_end += shift
            strip.frame_start = start
        return strip

    try:
        if previous is not None and animation.use_nla:
            strip = add_strip(previous, previous_slot, "原活动动作")
            strip.extrapolation = animation.action_extrapolation
            strip.blend_type = animation.action_blend_type
            if animation.action_influence < 1.0:
                strip.influence = animation.action_influence
                strip.use_animated_influence = True
        strip = add_strip(action, action.slots[0], "MotionBricks")
        strip.extrapolation = "NOTHING"
        if animation.use_nla:
            animation.action = None
        for bone in adapter.bones:
            bone.rotation_mode = "QUATERNION"
    except Exception:
        for track in reversed(created):
            tracks.remove(track)
        if not had_animation:
            rig.animation_data_clear()
        elif animation.action != previous:
            animation.action = previous
            if previous is not None:
                animation.action_slot = previous_slot
        raise
