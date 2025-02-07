#!/usr/bin/env python
import threading
import numpy as np
import resource_retriever

import cv2
from cv_bridge import CvBridge
import rospy
from sensor_msgs.msg import Image
from std_msgs.msg import Int64MultiArray
from sensor_msgs.msg import RegionOfInterest

from mask_rcnn_ros.mrcnn.config import Config
from mask_rcnn_ros.mrcnn import model as modellib
from mask_rcnn_ros.mrcnn import visualize
from mask_rcnn_ros.msg import Result

class InferenceConfig(Config):
    # Set batch size to 1 since we'll be running inference on
    # one image at a time. Batch size = GPU_COUNT * IMAGES_PER_GPU
    NAME = "coco"
    NUM_CLASSES = 1 + 80
    DETECTION_MIN_CONFIDENCE = 0
    RPN_ANCHOR_SCALES = (32, 64, 128, 256, 384)
    GPU_COUNT = 1
    IMAGES_PER_GPU = 1

class MaskRCNNNode(object):
    def __init__(self):
        self._cv_bridge = CvBridge()

        config = InferenceConfig()
        config.display()

        self._visualization = rospy.get_param('~visualization', True)


        # Create model object in inference mode.
        self._model = modellib.MaskRCNN(mode="inference", model_dir="",
                                        config=config)
        # Load weights trained on MS-COCO
        model_path = resource_retriever.get_filename(rospy.get_param('~weight_location'), use_protocol=False)

        rospy.loginfo("Loading pretrained model into memory")
        self._model.load_weights(model_path, by_name=True)
        rospy.loginfo("Successfully loaded pretrained model into memory")

        self._class_names = rospy.get_param('~class_names')

        self._last_msg = None
        self._msg_lock = threading.Lock()

        self._class_colors = visualize.random_colors(len(self._class_names))

        self._publish_rate = rospy.get_param('~publish_rate', 100)

        # Start ROS publishers
        self._result_pub = \
            rospy.Publisher(
                rospy.get_param('~topic_publishing') + "/result",
                Result,
                queue_size=1
        )

        self._vis_pub = \
            rospy.Publisher(
                rospy.get_param('~topic_publishing') + "/visualization",
                Image,
                queue_size=1
        )

        # Start ROS subscriber
        image_sub = rospy.Subscriber(
            '~cameraTopic',
            Image, 
            self._image_callback,
            queue_size=1
        )

        rospy.loginfo("Running Mask-RCNN...  (Listening to camera topic: '{}')".format(image_sub.name))

    def run(self):
        rate = rospy.Rate(self._publish_rate)
        while not rospy.is_shutdown():
            if self._msg_lock.acquire(False):
                msg = self._last_msg
                self._last_msg = None
                self._msg_lock.release()
            else:
                rate.sleep()
                continue

            if msg is not None:
                np_image = self._cv_bridge.imgmsg_to_cv2(msg, 'bgr8')

                # Run detection
                results = self._model.detect([np_image], verbose=0)
                result = results[0]
                result_msg = self._build_result_msg(msg, result)
                self._result_pub.publish(result_msg)

                # Visualize results
                if self._visualization:
                    vis_image = self._visualize(result, np_image)
                    cv_result = np.zeros(shape=vis_image.shape, dtype=np.uint8)
                    cv2.convertScaleAbs(vis_image, cv_result)
                    image_msg = self._cv_bridge.cv2_to_imgmsg(cv_result, 'bgr8')
                    self._vis_pub.publish(image_msg)

            rate.sleep()

    def _build_result_msg(self, msg, result):
        result_msg = Result()
        result_msg.header = msg.header
        for i, (y1, x1, y2, x2) in enumerate(result['rois']):
            box = RegionOfInterest()
            box.x_offset = x1.item()
            box.y_offset = y1.item()
            box.height = (y2 - y1).item()
            box.width = (x2 - x1).item()
            result_msg.boxes.append(box)

            class_id = result['class_ids'][i]
            result_msg.class_ids.append(class_id)

            class_name = self._class_names[class_id]
            result_msg.class_names.append(class_name)

            score = result['scores'][i]
            result_msg.scores.append(score)

            mask = Int64MultiArray()
            mask_msg = np.zeros(result['masks'].shape[:2], np.int64)
            mask_msg[result['masks'][:,:,i]==True] = np.int64(class_id)
            mask_msg_list = mask_msg.tolist()
            mask.data = [item for sublist in mask_msg_list for item in sublist]
            result_msg.masks.append(mask)
        return result_msg

    def _visualize(self, result, image):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        fig = Figure()
        canvas = FigureCanvasAgg(fig)
        axes = fig.gca()
        visualize.display_instances(image, result['rois'], result['masks'],
                                    result['class_ids'], self._class_names,
                                    result['scores'], ax=axes,
                                    colors=self._class_colors)
        fig.tight_layout()
        canvas.draw()
        result = np.frombuffer(canvas.tostring_rgb(), dtype='uint8')

        _, _, w, h = fig.bbox.bounds
        result = result.reshape((int(h), int(w), 3))
        return result

    def _image_callback(self, msg):
        rospy.logdebug("Get an image")
        if self._msg_lock.acquire(False):
            self._last_msg = msg
            self._msg_lock.release()

def main():
    rospy.init_node('mask_rcnn')

    node = MaskRCNNNode()
    node.run()

if __name__ == '__main__':
    main()
