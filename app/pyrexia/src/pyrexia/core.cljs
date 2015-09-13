(ns ^:figwheel-always pyrexia.core
    (:require
        [clojure.walk :refer [keywordize-keys]]
        [goog.Timer :as timer]
        [goog.events :as events]
        [cognitect.transit :as t]
        [om.core :as om]
        [om.dom :as dom]
		[cljs-time.core :as time]
		[cljs-time.format :as tf]
    )
    (:require-macros [pyrexia.env :as env :refer [cljs-env]])
    (:import [goog.net XhrIo])
)

(enable-console-print!)

(defonce app-state (atom {
    :nodes []
    :timer nil
}))

(def r (t/reader :json))
(def w (t/writer :json-verbose))

(defn on-js-reload []
  ;; optionally touch your app-state to force rerendering depending on
  ;; your application
  ;; (swap! app-state update-in [:__figwheel_counter] inc)
)

(def search-query "{\"size\":0,\"aggs\":{\"group_by_state\":{\"terms\":{\"field\":\"id\"},\"aggs\":{\"top_tag_hits\":{\"top_hits\":{\"sort\":[{\"@timestamp\":{\"order\":\"desc\"}}],\"size\":1}}}}}}")

(def logstashFormatter (tf/formatter "yyyy.MM.dd"))
(defn logName [date]
	(str "temperature-" (tf/unparse logstashFormatter date))
)

(defn retrieve
  [log payload callback error-callback]
  (let [xhr (XhrIo.)]
     (events/listen xhr goog.net.EventType.COMPLETE
       (fn [e]
         (callback (t/read r (.getResponseText xhr)))))
     (. xhr
       (send (str "http://" (cljs-env :es-host "localhost") ":9200/" log "/_search") "POST" payload))
	   ))

(defn parse-nodes [node-data node-key]
    (let [
        buckets (-> node-data :aggregations :group_by_state :buckets)
        nodes (apply merge (map #(hash-map (:key %) (-> % :top_tag_hits :hits :hits first :_source)) buckets))
    ]
        (.log js/console (pr-str nodes))
        (swap! app-state assoc node-key nodes)
		(swap! app-state assoc :nodes (merge (:old-nodes @app-state) (:new-nodes @app-state)))
    )
)

(defn fetch-events []
    (retrieve (logName (time/now)) search-query #(parse-nodes (-> % keywordize-keys ) :new-nodes) #() )
    (retrieve (-> (time/now) (time/minus (time/days 1)) logName) search-query #(parse-nodes (-> % keywordize-keys ) :old-nodes) #() )
)

(defn poll
  []
  (let [timer (goog.Timer. 5000)]
    (do
        (fetch-events)
        (. timer (start))
        (swap! app-state assoc :timer timer)
        (events/listen timer goog.Timer/TICK fetch-events))))

(if (-> (:timer @app-state) nil? not)
    (.stop (:timer @app-state))
)
(poll)

(defn sensor-view [sensor owner]
  (reify
    om/IRender
    (render [this]
      (dom/li nil (first sensor)
        (apply dom/ul nil (map
            #(dom/li nil (-> % first name) " : " (-> % second))
            (select-keys (second sensor) [:temp :humid (keyword "@timestamp")])
        ))))))

(defn sensors-view [data owner]
  (reify
    om/IRender
    (render [this]
        (apply dom/ul nil
          (om/build-all sensor-view (:nodes data))))))

(om/root sensors-view app-state
  {:target (. js/document (getElementById "sensors"))})
